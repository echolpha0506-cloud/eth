import time
import requests
import pandas as pd
from datetime import datetime
from collections import deque

# ==================== 配置区 ====================
SERVERCHAN_SENDKEY = "SCT440554TBR9un4ZZPUOu6r28LJvNDpY8"

SYMBOL = "ETH-USDT"
CHECK_INTERVAL = 20
MIN_BUY_SELL_RATIO = 2.5
MIN_SELL_BUY_RATIO = 2.5

NEAR_LOWER_15M = 0.35
NEAR_LOWER_5M = 0.25
NEAR_LOWER_3M = 0.20

NEAR_UPPER_15M = 0.35
NEAR_UPPER_5M = 0.25
NEAR_UPPER_3M = 0.20

SIGNAL_COOLDOWN = 300

# ==================== 成功率统计 ====================
pending_signals = deque()
stats_3m = {"win": 0, "total": 0}
stats_5m = {"win": 0, "total": 0}

def send_wechat(title: str, content: str):
    url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"
    data = {"title": title, "desp": content}
    try:
        resp = requests.post(url, data=data, timeout=10)
        print("微信提醒发送成功" if resp.json().get("code") == 0 else f"发送失败: {resp.text}")
    except Exception as e:
        print(f"微信发送异常: {e}")

def get_okx_orderbook():
    try:
        url = f"https://www.okx.com/api/v5/market/books?instId={SYMBOL}&sz=10"
        data = requests.get(url, timeout=5).json()
        if data.get("code") != "0" or not data.get("data"):
            return 0, 0
        bids = sum(float(p) * float(q) for p, q, _, _ in data["data"][0]["bids"])
        asks = sum(float(p) * float(q) for p, q, _, _ in data["data"][0]["asks"])
        return bids, asks
    except Exception as e:
        print(f"欧意订单簿错误: {e}")
        return 0, 0

def get_okx_klines(interval="15m", limit=50):
    try:
        url = f"https://www.okx.com/api/v5/market/candles?instId={SYMBOL}&bar={interval}&limit={limit}"
        data = requests.get(url, timeout=5).json()
        if data.get("code") != "0" or not data.get("data"):
            raise ValueError("K线数据异常")
        rows = data["data"][::-1]
        df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "vol", "volCcy", "volCcyQuote", "confirm"])
        df["close"] = df["close"].astype(float)
        return df
    except Exception as e:
        print(f"K线获取失败: {e}")
        return None

def get_current_price():
    try:
        url = f"https://www.okx.com/api/v5/market/ticker?instId={SYMBOL}"
        data = requests.get(url, timeout=5).json()
        if data.get("code") == "0" and data.get("data"):
            return float(data["data"][0]["last"])
    except:
        pass
    return None

def calculate_bollinger(df, period=20, std=2):
    df = df.copy()
    df["ma"] = df["close"].rolling(period).mean()
    df["std"] = df["close"].rolling(period).std()
    df["upper"] = df["ma"] + std * df["std"]
    df["lower"] = df["ma"] - std * df["std"]
    return df

def get_bb_info(interval):
    df = get_okx_klines(interval)
    if df is None or len(df) < 20:
        return None
    df = calculate_bollinger(df)
    latest = df.iloc[-1]
    price = latest["close"]
    lower = latest["lower"]
    upper = latest["upper"]
    mid = latest["ma"]
    dist_lower = (price - lower) / price * 100 if price > 0 else 999
    dist_upper = (upper - price) / price * 100 if price > 0 else 999
    return {
        "price": price,
        "lower": lower,
        "upper": upper,
        "mid": mid,
        "dist_lower": dist_lower,
        "dist_upper": dist_upper
    }

def check_pending_signals():
    global stats_3m, stats_5m
    now = datetime.now()
    current_price = get_current_price()
    if current_price is None:
        return

    remaining = deque()
    for sig in pending_signals:
        elapsed = (now - sig["time"]).total_seconds()

        if not sig["checked_3m"] and elapsed >= 180:
            win = (current_price > sig["price"]) if sig["direction"] == "long" else (current_price < sig["price"])
            stats_3m["total"] += 1
            if win:
                stats_3m["win"] += 1
            sig["checked_3m"] = True
            rate_3m = stats_3m["win"] / stats_3m["total"] * 100 if stats_3m["total"] > 0 else 0
            print(f">>> 3分钟验证: {'成功' if win else '失败'} | 当前3m成功率: {rate_3m:.1f}% ({stats_3m['win']}/{stats_3m['total']})")

        if not sig["checked_5m"] and elapsed >= 300:
            win = (current_price > sig["price"]) if sig["direction"] == "long" else (current_price < sig["price"])
            stats_5m["total"] += 1
            if win:
                stats_5m["win"] += 1
            sig["checked_5m"] = True
            rate_5m = stats_5m["win"] / stats_5m["total"] * 100 if stats_5m["total"] > 0 else 0
            print(f">>> 5分钟验证: {'成功' if win else '失败'} | 当前5m成功率: {rate_5m:.1f}% ({stats_5m['win']}/{stats_5m['total']})")

        if not (sig["checked_3m"] and sig["checked_5m"]):
            remaining.append(sig)

    pending_signals.clear()
    pending_signals.extend(remaining)

def get_success_rate_text():
    rate_3m = stats_3m["win"] / stats_3m["total"] * 100 if stats_3m["total"] > 0 else 0
    rate_5m = stats_5m["win"] / stats_5m["total"] * 100 if stats_5m["total"] > 0 else 0
    return f"3分钟成功率: {rate_3m:.1f}% ({stats_3m['win']}/{stats_3m['total']})\n5分钟成功率: {rate_5m:.1f}% ({stats_5m['win']}/{stats_5m['total']})"

print("=" * 50)
print("ETH 事件合约信号监控已启动（含成功率统计）")
print("=" * 50)

last_long_time = 0
last_short_time = 0

while True:
    try:
        check_pending_signals()

        ok_bids, ok_asks = get_okx_orderbook()
        buy_sell_ratio = ok_bids / ok_asks if ok_asks > 0 else 0
        sell_buy_ratio = ok_asks / ok_bids if ok_bids > 0 else 0

        bb_15 = get_bb_info("15m")
        bb_5  = get_bb_info("5m")
        bb_3  = get_bb_info("3m")

        if bb_15 is None:
            print("布林带数据获取失败，跳过本轮")
            time.sleep(CHECK_INTERVAL)
            continue

        price = bb_15["price"]
        now = datetime.now().strftime("%H:%M:%S")

        near_lower_15 = bb_15["dist_lower"] < NEAR_LOWER_15M and price < bb_15["mid"]
        near_lower_short = False
        if bb_5:
            near_lower_short = near_lower_short or (bb_5["dist_lower"] < NEAR_LOWER_5M)
        if bb_3:
            near_lower_short = near_lower_short or (bb_3["dist_lower"] < NEAR_LOWER_3M)
        long_signal = (buy_sell_ratio >= MIN_BUY_SELL_RATIO) and near_lower_15 and near_lower_short

        near_upper_15 = bb_15["dist_upper"] < NEAR_UPPER_15M and price > bb_15["mid"]
        near_upper_short = False
        if bb_5:
            near_upper_short = near_upper_short or (bb_5["dist_upper"] < NEAR_UPPER_5M)
        if bb_3:
            near_upper_short = near_upper_short or (bb_3["dist_upper"] < NEAR_UPPER_3M)
        short_signal = (sell_buy_ratio >= MIN_SELL_BUY_RATIO) and near_upper_15 and near_upper_short

        rate_text = get_success_rate_text()
        print(f"[{now}] ETH价格:{price:.2f} | 下轨:{bb_15['dist_lower']:.2f}% | 上轨:{bb_15['dist_upper']:.2f}% | 买/卖:{buy_sell_ratio:.2f}")
        print(f"       {rate_text}")

        if long_signal:
            current_time = time.time()
            if current_time - last_long_time > SIGNAL_COOLDOWN:
                pending_signals.append({
                    "time": datetime.now(),
                    "price": price,
                    "direction": "long",
                    "checked_3m": False,
                    "checked_5m": False
                })
                title = "【ETH做多信号】事件合约看涨"
                content = f"""
**时间**：{now}
**ETH当前价格**：{price:.2f}

**距离下轨**：
- 15分钟：{bb_15['dist_lower']:.2f}%
- 5分钟：{bb_5['dist_lower']:.2f}% if bb_5 else 'N/A'
- 3分钟：{bb_3['dist_lower']:.2f}% if bb_3 else 'N/A'

**欧意买/卖比**：{buy_sell_ratio:.2f}

**历史成功率**：
{rate_text}

建议：最小仓位开【看涨】3分钟或5分钟事件合约
"""
                send_wechat(title, content)
                print(">>> ETH做多信号已推送微信！")
                last_long_time = current_time

        if short_signal:
            current_time = time.time()
            if current_time - last_short_time > SIGNAL_COOLDOWN:
                pending_signals.append({
                    "time": datetime.now(),
                    "price": price,
                    "direction": "short",
                    "checked_3m": False,
                    "checked_5m": False
                })
                title = "【ETH做空信号】事件合约看跌"
                content = f"""
**时间**：{now}
**ETH当前价格**：{price:.2f}

**距离上轨**：
- 15分钟：{bb_15['dist_upper']:.2f}%
- 5分钟：{bb_5['dist_upper']:.2f}% if bb_5 else 'N/A'
- 3分钟：{bb_3['dist_upper']:.2f}% if bb_3 else 'N/A'

**欧意卖/买比**：{sell_buy_ratio:.2f}

**历史成功率**：
{rate_text}

建议：最小仓位开【看跌】3分钟或5分钟事件合约
"""
                send_wechat(title, content)
                print(">>> ETH做空信号已推送微信！")
                last_short_time = current_time

        if not long_signal and not short_signal:
            print("暂无信号")

    except Exception as e:
        print(f"主循环错误: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)

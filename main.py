import time
import requests
import pandas as pd
from datetime import datetime
from collections import deque

# ==================== 配置区 ====================
SERVERCHAN_SENDKEY = "SCT440554TBR9un4ZZPUOu6r28LJvNDpY8"

SYMBOL = "ETH-USDT"
CHECK_INTERVAL = 20
MIN_BUY_SELL_RATIO = 4.0
MIN_SELL_BUY_RATIO = 4.0

NEAR_LOWER_15M = 0.12
NEAR_LOWER_5M  = 0.10
NEAR_LOWER_3M  = 0.08

NEAR_UPPER_15M = 0.12
NEAR_UPPER_5M  = 0.10
NEAR_UPPER_3M  = 0.08

SIGNAL_COOLDOWN = 180
PRICE_DIFF_LIMIT = 0.15
BBW_RANGE_THRESHOLD = 2.0

pending_signals = deque()
stats_3m = {"win": 0, "total": 0}
stats_5m = {"win": 0, "total": 0}

last_long_price = 0
last_short_price = 0
last_long_time = 0
last_short_time = 0

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

def get_okx_klines(interval="15m", limit=100):
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
    if df is None or len(df) < 30:
        return None
    df = calculate_bollinger(df)
    latest = df.iloc[-1]
    price = latest["close"]
    lower = latest["lower"]
    upper = latest["upper"]
    mid = latest["ma"]
    dist_lower = (price - lower) / price * 100 if price > 0 else 999
    dist_upper = (upper - price) / price * 100 if price > 0 else 999
    bbw = (upper - lower) / mid * 100 if mid > 0 else 999
    is_ranging = bbw < BBW_RANGE_THRESHOLD
    return {
        "price": price,
        "lower": lower,
        "upper": upper,
        "mid": mid,
        "dist_lower": dist_lower,
        "dist_upper": dist_upper,
        "bbw": bbw,
        "is_ranging": is_ranging
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
        direction_text = "做多" if sig["direction"] == "long" else "做空"

        if not sig["checked_3m"] and elapsed >= 180:
            win = (current_price > sig["price"]) if sig["direction"] == "long" else (current_price < sig["price"])
            stats_3m["total"] += 1
            if win:
                stats_3m["win"] += 1
            sig["checked_3m"] = True
            rate_3m = stats_3m["win"] / stats_3m["total"] * 100 if stats_3m["total"] > 0 else 0
            result = "成功 ✅" if win else "失败 ❌"
            title = f"【3分钟验证】ETH {direction_text}信号"
            content = f"""
**验证时间**：{now.strftime('%H:%M:%S')}
**信号方向**：{direction_text}
**开仓价格**：{sig['price']:.2f}
**当前价格**：{current_price:.2f}
**结果**：{result}

**当前3分钟成功率**：{rate_3m:.1f}%（{stats_3m['win']}/{stats_3m['total']}）
"""
            send_wechat(title, content)
            print(f">>> 3分钟验证: {result} | 成功率: {rate_3m:.1f}%")

        if not sig["checked_5m"] and elapsed >= 300:
            win = (current_price > sig["price"]) if sig["direction"] == "long" else (current_price < sig["price"])
            stats_5m["total"] += 1
            if win:
                stats_5m["win"] += 1
            sig["checked_5m"] = True
            rate_5m = stats_5m["win"] / stats_5m["total"] * 100 if stats_5m["total"] > 0 else 0
            result = "成功 ✅" if win else "失败 ❌"
            title = f"【5分钟验证】ETH {direction_text}信号"
            content = f"""
**验证时间**：{now.strftime('%H:%M:%S')}
**信号方向**：{direction_text}
**开仓价格**：{sig['price']:.2f}
**当前价格**：{current_price:.2f}
**结果**：{result}

**当前5分钟成功率**：{rate_5m:.1f}%（{stats_5m['win']}/{stats_5m['total']}）
"""
            send_wechat(title, content)
            print(f">>> 5分钟验证: {result} | 成功率: {rate_5m:.1f}%")

        if not (sig["checked_3m"] and sig["checked_5m"]):
            remaining.append(sig)

    pending_signals.clear()
    pending_signals.extend(remaining)

def get_success_rate_text():
    rate_3m = stats_3m["win"] / stats_3m["total"] * 100 if stats_3m["total"] > 0 else 0
    rate_5m = stats_5m["win"] / stats_5m["total"] * 100 if stats_5m["total"] > 0 else 0
    return f"3分钟成功率: {rate_3m:.1f}% ({stats_3m['win']}/{stats_3m['total']})\n5分钟成功率: {rate_5m:.1f}% ({stats_5m['win']}/{stats_5m['total']})"

def get_range_tag(r3, r5, r15):
    count = sum([r3, r5, r15])
    if count == 3:
        return "【超强震荡信号】"
    elif count == 2:
        return "【强震荡信号】"
    else:
        return ""

print("=" * 50)
print("ETH 事件合约信号监控已启动（震荡判断+信号加强）")
print("=" * 50)

while True:
    try:
        check_pending_signals()

        ok_bids, ok_asks = get_okx_orderbook()
        buy_sell_ratio = ok_bids / ok_asks if ok_asks > 0 else 0
        sell_buy_ratio = ok_asks / ok_bids if ok_bids > 0 else 0

        bb_15 = get_bb_info("15m")
        bb_5  = get_bb_info("5m")
        bb_3  = get_bb_info("3m")

        if bb_15 is None or bb_5 is None or bb_3 is None:
            print("布林带数据获取失败，跳过本轮")
            time.sleep(CHECK_INTERVAL)
            continue

        price = bb_15["price"]
        now_str = datetime.now().strftime("%H:%M:%S")
        now_ts = time.time()

        r15 = bb_15["is_ranging"]
        r5  = bb_5["is_ranging"]
        r3  = bb_3["is_ranging"]
        range_tag = get_range_tag(r3, r5, r15)

        status_15 = "震荡" if r15 else "趋势"
        status_5  = "震荡" if r5 else "趋势"
        status_3  = "震荡" if r3 else "趋势"

        near_lower_15 = bb_15["dist_lower"] < NEAR_LOWER_15M and price < bb_15["mid"]
        near_lower_5  = bb_5["dist_lower"] < NEAR_LOWER_5M
        near_lower_3  = bb_3["dist_lower"] < NEAR_LOWER_3M
        long_signal = (buy_sell_ratio >= MIN_BUY_SELL_RATIO) and near_lower_15 and near_lower_5 and near_lower_3

        near_upper_15 = bb_15["dist_upper"] < NEAR_UPPER_15M and price > bb_15["mid"]
        near_upper_5  = bb_5["dist_upper"] < NEAR_UPPER_5M
        near_upper_3  = bb_3["dist_upper"] < NEAR_UPPER_3M
        short_signal = (sell_buy_ratio >= MIN_SELL_BUY_RATIO) and near_upper_15 and near_upper_5 and near_upper_3

        rate_text = get_success_rate_text()
        print(f"[{now_str}] ETH:{price:.2f} | 下轨:{bb_15['dist_lower']:.2f}% | 上轨:{bb_15['dist_upper']:.2f}% | 买/卖:{buy_sell_ratio:.2f}")
        print(f"       3m:{status_3} 5m:{status_5} 15m:{status_15} | {rate_text}")

        if long_signal:
            price_diff = abs(price - last_long_price) / price * 100 if last_long_price > 0 else 999
            if (now_ts - last_long_time > SIGNAL_COOLDOWN) and (price_diff > PRICE_DIFF_LIMIT):
                pending_signals.append({
                    "time": datetime.now(),
                    "price": price,
                    "direction": "long",
                    "checked_3m": False,
                    "checked_5m": False
                })
                title = f"{range_tag}【ETH做多信号】事件合约看涨"
                content = f"""
**时间**：{now_str}
**ETH当前价格**：{price:.2f}

**距离下轨**：
- 15分钟：{bb_15['dist_lower']:.2f}%（{status_15}）
- 5分钟：{bb_5['dist_lower']:.2f}%（{status_5}）
- 3分钟：{bb_3['dist_lower']:.2f}%（{status_3}）

**欧意买/卖比**：{buy_sell_ratio:.2f}

**历史成功率**：
{rate_text}

建议：最小仓位开【看涨】3分钟或5分钟事件合约
"""
                send_wechat(title, content)
                print(f">>> ETH做多信号已推送微信！{range_tag}")
                last_long_time = now_ts
                last_long_price = price
            else:
                print("（做多信号被防重复过滤）")

        if short_signal:
            price_diff = abs(price - last_short_price) / price * 100 if last_short_price > 0 else 999
            if (now_ts - last_short_time > SIGNAL_COOLDOWN) and (price_diff > PRICE_DIFF_LIMIT):
                pending_signals.append({
                    "time": datetime.now(),
                    "price": price,
                    "direction": "short",
                    "checked_3m": False,
                    "checked_5m": False
                })
                title = f"{range_tag}【ETH做空信号】事件合约看跌"
                content = f"""
**时间**：{now_str}
**ETH当前价格**：{price:.2f}

**距离上轨**：
- 15分钟：{bb_15['dist_upper']:.2f}%（{status_15}）
- 5分钟：{bb_5['dist_upper']:.2f}%（{status_5}）
- 3分钟：{bb_3['dist_upper']:.2f}%（{status_3}）

**欧意卖/买比**：{sell_buy_ratio:.2f}

**历史成功率**：
{rate_text}

建议：最小仓位开【看跌】3分钟或5分钟事件合约
"""
                send_wechat(title, content)
                print(f">>> ETH做空信号已推送微信！{range_tag}")
                last_short_time = now_ts
                last_short_price = price
            else:
                print("（做空信号被防重复过滤）")

        if not long_signal and not short_signal:
            print("暂无信号")

    except Exception as e:
        print(f"主循环错误: {e}")
        time.sleep(10)

    time.sleep(CHECK_INTERVAL)

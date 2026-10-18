import os
import time
import re
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import ccxt
import pandas as pd
import requests
import numpy as np

# ==================== 配置 ====================
SERVERCHAN_SENDKEY = os.getenv("SERVERCHAN_SENDKEY", "")
CHECK_INTERVAL = 45
BB_PERIOD = 20
BB_STD = 2
FLAT_THRESHOLD = 0.00045
BID_ASK_RATIO = 4.5
SYMBOLS = ["BTC/USDT", "ETH/USDT"]
TIMEFRAMES = ["3m", "5m", "15m"]
PROXY = os.getenv("PROXY", "")
SIGNAL_COOLDOWN = 180          # 3分钟冷却

# GMT+8 时区
GMT8 = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ==================== 交易所 ====================
exchange_config = {"enableRateLimit": True, "options": {"defaultType": "spot"}}

if PROXY:
    exchange_config["proxies"] = {"http": PROXY, "https": PROXY}

okx = ccxt.okx(exchange_config)

binance = None
if PROXY:
    try:
        binance = ccxt.binance(exchange_config)
        logger.info("已启用币安（通过代理）")
    except Exception as e:
        logger.warning(f"币安初始化失败: {e}")

simulations: List[Dict] = []

# 模拟胜率统计
stats = {
    "total": 0,
    "win": 0
}

def now_gmt8():
    """返回当前 GMT+8 时间"""
    return datetime.now(GMT8)

def send_wechat(title: str, content: str):
    if not SERVERCHAN_SENDKEY:
        logger.warning("未配置 SERVERCHAN_SENDKEY，跳过推送")
        return

    if SERVERCHAN_SENDKEY.startswith("sctp"):
        match = re.search(r"sctp(\d+)t", SERVERCHAN_SENDKEY)
        if match:
            uid = match.group(1)
            url = f"https://{uid}.push.ft07.com/send/{SERVERCHAN_SENDKEY}.send"
        else:
            logger.error("无效的 sctp SendKey 格式")
            return
    else:
        url = f"https://sctapi.ftqq.com/{SERVERCHAN_SENDKEY}.send"

    payload = {"title": title, "desp": content}

    try:
        r = requests.post(url, data=payload, timeout=10)
        result = r.json()
        if result.get("code") == 0 or str(result.get("errno")) == "0":
            logger.info(f"Server酱推送成功: {title}")
        else:
            logger.error(f"Server酱推送失败: {result}")
    except Exception as e:
        logger.error(f"Server酱推送异常: {e}")

def calc_bollinger(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ma"] = df["close"].rolling(BB_PERIOD).mean()
    df["std"] = df["close"].rolling(BB_PERIOD).std()
    df["upper"] = df["ma"] + BB_STD * df["std"]
    df["lower"] = df["ma"] - BB_STD * df["std"]
    return df

def is_flat(ma_series: pd.Series, lookback: int = 8) -> bool:
    if len(ma_series) < lookback + 2:
        return False
    recent = ma_series.iloc[-lookback:]
    change = abs(recent.iloc[-1] - recent.iloc[0]) / recent.iloc[0]
    return change < FLAT_THRESHOLD

def get_orderbook_ratio(exchange, symbol: str, limit: int = 10) -> float:
    try:
        ob = exchange.fetch_order_book(symbol, limit=limit)
        bid_vol = sum([x[1] for x in ob["bids"][:limit]])
        ask_vol = sum([x[1] for x in ob["asks"][:limit]])
        if ask_vol == 0:
            return 999.0
        return bid_vol / ask_vol
    except Exception as e:
        logger.error(f"获取盘口失败 {exchange.id} {symbol}: {e}")
        return 0.0

def fetch_ohlcv(exchange, symbol: str, timeframe: str, limit: int = 50) -> pd.DataFrame:
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
    df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    return df

def check_signal(symbol: str) -> Optional[Dict]:
    try:
        dfs = {}
        for tf in TIMEFRAMES:
            dfs[tf] = calc_bollinger(fetch_ohlcv(okx, symbol, tf))

        flat_15 = is_flat(dfs["15m"]["ma"])
        flat_5 = is_flat(dfs["5m"]["ma"])
        flat_3 = is_flat(dfs["3m"]["ma"])

        if not (flat_15 and flat_5 and flat_3):
            return None

        last = dfs["15m"].iloc[-1]
        price = float(last["close"])
        mid = float(last["ma"])
        lower = float(last["lower"])
        upper = float(last["upper"])

        near_mid = abs(price - mid) / mid < 0.0010
        near_lower = abs(price - lower) / lower < 0.0015
        near_upper = abs(price - upper) / upper < 0.0015

        okx_ratio = get_orderbook_ratio(okx, symbol)
        binance_ratio = okx_ratio

        if binance:
            try:
                binance_ratio = get_orderbook_ratio(binance, symbol)
            except Exception:
                pass

        direction = None
        reason = []

        if (near_mid or near_lower) and okx_ratio >= BID_ASK_RATIO and binance_ratio >= BID_ASK_RATIO:
            direction = "long"
            reason.append(f"回踩中/下轨 + 两边买盘占优 (OKX {okx_ratio:.1f}x 币安{binance_ratio:.1f}x)")
        elif near_upper and okx_ratio <= (1 / BID_ASK_RATIO) and binance_ratio <= (1 / BID_ASK_RATIO):
            direction = "short"
            reason.append("触及上轨 + 两边卖盘占优")

        if direction:
            return {
                "symbol": symbol,
                "direction": direction,
                "price": price,
                "mid": mid,
                "lower": lower,
                "upper": upper,
                "binance_ratio": binance_ratio,
                "okx_ratio": okx_ratio,
                "flat": f"15m:{flat_15} 5m:{flat_5} 3m:{flat_3}",
                "reason": " | ".join(reason),
                "time": now_gmt8().strftime("%Y-%m-%d %H:%M:%S")
            }
        return None
    except Exception as e:
        logger.error(f"检查信号失败 {symbol}: {e}")
        return None

def start_simulation(signal: Dict):
    now = now_gmt8()
    for minutes in [3, 5]:
        sim = {
            "id": f"{signal['symbol']}_{signal['direction']}_{minutes}m_{int(now.timestamp())}",
            "symbol": signal["symbol"],
            "direction": signal["direction"],
            "entry_price": signal["price"],
            "entry_time": now,
            "expire_time": now + timedelta(minutes=minutes),
            "minutes": minutes,
            "status": "open",
            "result": None,
            "exit_price": None
        }
        simulations.append(sim)
        logger.info(f"启动模拟: {sim['id']}")

def check_simulations():
    now = now_gmt8()
    for sim in simulations[:]:
        if sim["status"] != "open":
            continue
        if now >= sim["expire_time"]:
            try:
                ticker = okx.fetch_ticker(sim["symbol"])
                exit_price = float(ticker["last"])
                sim["exit_price"] = exit_price
                if sim["direction"] == "long":
                    win = exit_price > sim["entry_price"]
                else:
                    win = exit_price < sim["entry_price"]
                sim["result"] = "WIN" if win else "LOSS"
                sim["status"] = "closed"

                stats["total"] += 1
                if win:
                    stats["win"] += 1

                win_rate = (stats["win"] / stats["total"] * 100) if stats["total"] > 0 else 0

                title = f"【模拟结算】{sim['symbol']} {sim['minutes']}分钟 {sim['result']}"
                content = f"""
**品种**: {sim['symbol']}
**方向**: {sim['direction']}
**开仓价**: {sim['entry_price']:.2f}
**结算价**: {exit_price:.2f}
**时长**: {sim['minutes']}分钟
**结果**: **{sim['result']}**

---
**当前模拟胜率**: {stats['win']}/{stats['total']} = **{win_rate:.1f}%**
"""
                send_wechat(title, content)
                logger.info(f"模拟结算: {sim['id']} → {sim['result']} | 胜率 {win_rate:.1f}% ({stats['win']}/{stats['total']})")
            except Exception as e:
                logger.error(f"结算失败: {e}")

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
    def log_message(self, format, *args):
        pass

def start_health_server():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Health server running on port {port}")

def main():
    logger.info("信号机器人启动（提高胜率版 + GMT+8 + 胜率统计），监控 BTC + ETH")
    start_health_server()

    last_signal_time = {}
    loop_count = 0

    while True:
        try:
            loop_count += 1
            now_str = now_gmt8().strftime("%H:%M:%S")
            logger.info(f"【心跳】第 {loop_count} 轮检查开始 ({now_str})")

            check_simulations()

            for symbol in SYMBOLS:
                signal = check_signal(symbol)
                if signal:
                    key = f"{symbol}_{signal['direction']}"
                    if key in last_signal_time and (now_gmt8() - last_signal_time[key]).seconds < SIGNAL_COOLDOWN:
                        continue

                    last_signal_time[key] = now_gmt8()

                    title = f"【开单信号】{symbol} {'做多' if signal['direction']=='long' else '做空'}"
                    content = f"""
**时间**: {signal['time']} (GMT+8)
**品种**: {symbol}
**方向**: **{'做多 (买涨)' if signal['direction']=='long' else '做空 (买跌)'}**
**当前价**: {signal['price']:.2f}
**15分中轨**: {signal['mid']:.2f}
**盘口**: OKX={signal['okx_ratio']:.1f}  币安={signal['binance_ratio']:.1f}
**布林走平**: {signal['flat']}
**理由**: {signal['reason']}

已自动启动 **3分钟 + 5分钟** 事件合约模拟，到期后会推送结果。
"""
                    send_wechat(title, content)
                    start_simulation(signal)
                    logger.info(f"发出信号: {title}")

            if loop_count % 10 == 0 and stats["total"] > 0:
                win_rate = stats["win"] / stats["total"] * 100
                logger.info(f"【胜率统计】当前模拟胜率: {stats['win']}/{stats['total']} = {win_rate:.1f}%")

            logger.info(f"【心跳】第 {loop_count} 轮检查完成，休眠 {CHECK_INTERVAL} 秒")
            time.sleep(CHECK_INTERVAL)

        except Exception as e:
            logger.error(f"主循环异常: {e}")
            time.sleep(30)

if __name__ == "__main__":
    main()

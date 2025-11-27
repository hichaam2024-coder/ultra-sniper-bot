# main.py
# Ultra Sniper Pro Max++ Final — Private-only edition (Final)
# Requirements:
# pip install aiogram aiohttp pandas numpy jinja2

import os
import asyncio
import time
import json
import traceback
import sqlite3
from typing import Dict, Any, Optional, List, Tuple
from collections import defaultdict
import math

import aiohttp
import numpy as np
import pandas as pd
from aiogram import Bot, Dispatcher, types
from aiogram.client.default import DefaultBotProperties
from aiogram.utils.keyboard import InlineKeyboardBuilder

from aiohttp import web
from jinja2 import Template

# -------------------------
# CONFIG / SECRETS
# -------------------------
BOT_NAME = "Ultra Sniper Pro Max++ Final"
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")  # must be your Telegram user id (number)
VIP_CHAT_ID = os.getenv("VIP_CHAT_ID")  # optional (ignored by default)
if not BOT_TOKEN:
    raise SystemExit("ضع BOT_TOKEN في البيئة (Secrets) قبل التشغيل.")
if not CHAT_ID:
    raise SystemExit("ضع CHAT_ID في البيئة (Secrets) قبل التشغيل (رقم user id).")

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

# -------------------------
# SETTINGS (editable)
# -------------------------
DEFAULT_SETTINGS = {
    "SNIPER_SCORE_THRESHOLD": 80,
    "CONFIDENCE_THRESHOLD": 72,
    "PRICE_MOVE_THRESHOLD": 0.6,
    "COOLDOWN_SECONDS": 600,
    "MONITOR_INTERVAL": 12,
    "ALLOCATION_SIM": 5.0,
    "ALERT_SOUND": True,
    "RETRY_ATTEMPTS": 2,
    "RETRY_BACKOFF": 1.2,
    "HTTP_CONN_LIMIT": 80,
    "SYMBOL_WORKERS": 10,
    "LANG": "both",
    "MODE": "balanced",
    "VIP_MODE": False,
    "VIP_ONLY": False,
    "MIN_VOLUME_MULTIPLIER": 1.2,
    "MAX_SPREAD_PERCENT": 2.0,
    # new
    "SPEED_MODE": True,
    "CACHE_TTL_SEC": 8,
    "TOP_K": 3,
    "AUTO_LEARN": True,
    "NEWS_FILTER_ENABLED": True,
    "EXPANDED_PAIRS": True,
}
SETTINGS_FILE = "settings.json"
SETTINGS = DEFAULT_SETTINGS.copy()
if os.path.exists(SETTINGS_FILE):
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            SETTINGS.update(json.load(f))
    except Exception:
        pass
else:
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(SETTINGS, f, ensure_ascii=False, indent=2)

# read settings into vars
SNIPER_SCORE_THRESHOLD = SETTINGS["SNIPER_SCORE_THRESHOLD"]
CONFIDENCE_THRESHOLD = SETTINGS["CONFIDENCE_THRESHOLD"]
PRICE_MOVE_THRESHOLD = SETTINGS["PRICE_MOVE_THRESHOLD"] / 100.0
COOLDOWN_SECONDS = SETTINGS["COOLDOWN_SECONDS"]
MONITOR_INTERVAL = SETTINGS["MONITOR_INTERVAL"]
ALLOCATION_SIM = SETTINGS["ALLOCATION_SIM"]
ALERT_SOUND_ENABLED = SETTINGS["ALERT_SOUND"]
RETRY_ATTEMPTS = SETTINGS["RETRY_ATTEMPTS"]
RETRY_BACKOFF = SETTINGS["RETRY_BACKOFF"]
HTTP_CONN_LIMIT = SETTINGS["HTTP_CONN_LIMIT"]
SYMBOL_WORKERS = SETTINGS["SYMBOL_WORKERS"]
LANG = SETTINGS.get("LANG", "both")
MODE = SETTINGS.get("MODE", "balanced")
VIP_MODE = False  # private-only
VIP_ONLY = False
MIN_VOLUME_MULTIPLIER = SETTINGS.get("MIN_VOLUME_MULTIPLIER", 1.2)
MAX_SPREAD_PERCENT = SETTINGS.get("MAX_SPREAD_PERCENT", 2.0)

SPEED_MODE = SETTINGS.get("SPEED_MODE", True)
CACHE_TTL_SEC = SETTINGS.get("CACHE_TTL_SEC", 8)
TOP_K = SETTINGS.get("TOP_K", 3)
AUTO_LEARN = SETTINGS.get("AUTO_LEARN", True)
NEWS_FILTER_ENABLED = SETTINGS.get("NEWS_FILTER_ENABLED", True)
EXPANDED_PAIRS = SETTINGS.get("EXPANDED_PAIRS", True)

ALERT_SOUND_FILE = "alert.mp3"

# -------------------------
# GOLDEN LIST & EXPANDED PAIRS
# -------------------------
BASE_GOLDEN_LIST = [
    "BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT",
    "ADAUSDT","AVAXUSDT","LINKUSDT","DOTUSDT","MATICUSDT",
    "DOGEUSDT","LTCUSDT","ATOMUSDT","UNIUSDT","XAUUSD"
]

# Add common stablecoin pairs for more opportunities (won't reduce quality)
ADDITIONAL_PAIRS = [
    "USDCUSDT","BUSDUSDT","USDTUSD","USDCUSD","DAIUSDT",
    "FTMUSDT","SHIBUSDT","TRXUSDT","NEARUSDT","ICPUSDT"
]

GOLDEN_LIST = BASE_GOLDEN_LIST + (ADDITIONAL_PAIRS if EXPANDED_PAIRS else [])

EXCHANGE_APIS = {
    "binance": "https://api.binance.com/api/v3/ticker/price?symbol=",
    "binance_klines": "https://api.binance.com/api/v3/klines?symbol={symbol}&interval={interval}&limit={limit}",
    "kucoin":  "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=",
    "coinbase": "https://api.coinbase.com/v2/prices/{}-USD/spot",
    "kraken":  "https://api.kraken.com/0/public/Ticker?pair=",
    "bybit":   "https://api.bybit.com/v5/market/tickers?category=spot&symbol=",
    "bybit_kline": "https://api.bybit.com/spot/quote/v1/kline?symbol={symbol}&interval={interval}&limit={limit}",
    "okx":     "https://www.okx.com/api/v5/market/ticker?instId="
}
YAHOO_XAU = "https://query1.finance.yahoo.com/v7/finance/quote?symbols=XAUUSD=X"

CONV_LOG = "conversations_log.json"
SIGNALS_LOG = "signals_log.json"
DB_FILE = "signals.db"

# -------------------------
# DB init (signals + learn)
# -------------------------
def init_db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    c = conn.cursor()
    # signals table
    c.execute("""CREATE TABLE IF NOT EXISTS signals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time INTEGER,
        time_iso TEXT,
        symbol TEXT,
        score INTEGER,
        confidence REAL,
        reason TEXT,
        payload TEXT,
        vip INTEGER DEFAULT 0
    )""")
    # learn table: simple auto-learn history (store outcome tag if you later instrument)
    c.execute("""CREATE TABLE IF NOT EXISTS learn (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        time INTEGER,
        symbol TEXT,
        score INTEGER,
        confidence REAL,
        outcome TEXT
    )""")
    conn.commit()
    return conn

DB_CONN = init_db()

def db_insert_signal(time_ts: int, symbol: str, score: int, confidence: float, reason: str, payload: dict, vip: bool=False):
    try:
        cur = DB_CONN.cursor()
        cur.execute("INSERT INTO signals (time, time_iso, symbol, score, confidence, reason, payload, vip) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (time_ts, time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time_ts)), symbol, score, confidence, reason, json.dumps(payload, ensure_ascii=False), 1 if vip else 0))
        DB_CONN.commit()
    except Exception:
        traceback.print_exc()

def db_get_recent(limit: int = 50):
    try:
        cur = DB_CONN.cursor()
        cur.execute("SELECT time_iso, symbol, score, confidence, reason, vip FROM signals ORDER BY time DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        res = []
        for r in rows:
            res.append({"time_iso": r[0], "symbol": r[1], "score": r[2], "confidence": r[3], "reason": r[4], "vip": bool(r[5])})
        return res
    except Exception:
        # fallback: return empty
        return []

def db_export_csv(path: str = "signals_export.csv"):
    df = pd.read_sql_query("SELECT * FROM signals ORDER BY time DESC", DB_CONN)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path

def db_learn_insert(time_ts:int, symbol:str, score:int, confidence:float, outcome:str="unknown"):
    try:
        cur = DB_CONN.cursor()
        cur.execute("INSERT INTO learn (time, symbol, score, confidence, outcome) VALUES (?, ?, ?, ?, ?)",
                    (time_ts, symbol, score, confidence, outcome))
        DB_CONN.commit()
    except Exception:
        traceback.print_exc()

# -------------------------
# Utilities
# -------------------------
def append_json(path: str, obj: Any):
    try:
        arr = []
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                try:
                    arr = json.load(f)
                except:
                    arr = []
        arr.insert(0, obj)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(arr, f, ensure_ascii=False, indent=2)
    except:
        pass

def esc(s: Any) -> str:
    s = "" if s is None else str(s)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def now_iso() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

# -------------------------
# Connector + fetch helpers + Cache (Speed Mode)
# -------------------------
connector = None
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=10)

# Simple in-memory cache with TTL for speed mode
_cache_store: Dict[str, Tuple[float, Any]] = {}

def cache_get(key: str):
    if not SPEED_MODE:
        return None
    v = _cache_store.get(key)
    if not v:
        return None
    ts, val = v
    if time.time() - ts > CACHE_TTL_SEC:
        _cache_store.pop(key, None)
        return None
    return val

def cache_set(key: str, val: Any):
    if not SPEED_MODE:
        return
    _cache_store[key] = (time.time(), val)

async def safe_fetch(session: aiohttp.ClientSession, url: str, params: dict = None, retries: int = RETRY_ATTEMPTS):
    # Try cache first
    cache_key = f"GET:{url}:{json.dumps(params, sort_keys=True) if params else ''}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    backoff = 1.0
    for attempt in range(retries + 1):
        try:
            async with session.get(url, params=params, timeout=DEFAULT_TIMEOUT) as r:
                txt = await r.text()
                try:
                    parsed = json.loads(txt)
                except:
                    try:
                        parsed = await r.json()
                    except:
                        parsed = None
                cache_set(cache_key, parsed)
                return parsed
        except Exception:
            if attempt < retries:
                await asyncio.sleep(backoff)
                backoff *= RETRY_BACKOFF
            else:
                return None

# -------------------------
# Price aggregation
# -------------------------
async def fetch_prices(symbol: str) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    sym_up = symbol.upper()
    async with aiohttp.ClientSession(connector=connector) as session:
        async def _fetch_binance():
            j = await safe_fetch(session, EXCHANGE_APIS["binance"] + sym_up)
            p = (j.get("price") if j else None) if isinstance(j, dict) else None
            out["Binance"] = float(p) if p else None
        async def _fetch_kucoin():
            ku = sym_up.replace("USDT", "-USDT")
            j = await safe_fetch(session, EXCHANGE_APIS["kucoin"] + ku)
            p = None
            if isinstance(j, dict):
                p = (j.get("data") or {}).get("price") or j.get("price")
            out["KuCoin"] = float(p) if p else None
        async def _fetch_coinbase():
            if sym_up.endswith("USDT") or sym_up.endswith("USD"):
                c = sym_up.replace("USDT", "")
                j = await safe_fetch(session, EXCHANGE_APIS["coinbase"].format(c))
                p = (j.get("data") or {}).get("amount") if isinstance(j, dict) else None
                out["Coinbase"] = float(p) if p else None
            else:
                out["Coinbase"] = None
        async def _fetch_kraken():
            j = await safe_fetch(session, EXCHANGE_APIS["kraken"] + sym_up)
            p = None
            if isinstance(j, dict) and "result" in j:
                key = next(iter(j["result"].keys()))
                p = j["result"][key]["c"][0]
            out["Kraken"] = float(p) if p else None
        async def _fetch_bybit():
            j = await safe_fetch(session, EXCHANGE_APIS["bybit"] + sym_up)
            p = None
            if isinstance(j, dict):
                r = j.get("result")
                if isinstance(r, list) and len(r) > 0:
                    p = r[0].get("lastPrice") or r[0].get("last_price")
                elif isinstance(r, dict):
                    p = (r.get("list") or [{}])[0].get("lastPrice")
            out["Bybit"] = float(p) if p else None
        async def _fetch_okx():
            j = await safe_fetch(session, EXCHANGE_APIS["okx"] + sym_up)
            p = None
            if isinstance(j, dict):
                data = j.get("data")
                if isinstance(data, list) and len(data) > 0:
                    p = data[0].get("last")
            out["OKX"] = float(p) if p else None
        async def _fetch_xau():
            if sym_up.startswith("XAU"):
                j = await safe_fetch(session, YAHOO_XAU)
                p = None
                if isinstance(j, dict):
                    p = (j.get("quoteResponse") or {}).get("result", [{}])[0].get("regularMarketPrice")
                out["Yahoo_XAU"] = float(p) if p else None
            else:
                out["Yahoo_XAU"] = None

        tasks = [
            _fetch_binance(), _fetch_kucoin(), _fetch_coinbase(),
            _fetch_kraken(), _fetch_bybit(), _fetch_okx(), _fetch_xau()
        ]
        # gather with concurrency
        await asyncio.gather(*tasks)
    return out

# -------------------------
# Klines -> DataFrame
# -------------------------
KLINE_LIMIT = 240

async def get_klines_binance_df(symbol: str, interval: str = "5m", limit: int = KLINE_LIMIT) -> Optional[pd.DataFrame]:
    url = EXCHANGE_APIS["binance_klines"].format(symbol=symbol, interval=interval, limit=limit)
    async with aiohttp.ClientSession(connector=connector) as session:
        j = await safe_fetch(session, url)
        if isinstance(j, list) and len(j) > 0:
            try:
                df = pd.DataFrame(j)
                df = df.iloc[:, :6]
                df.columns = ["open_time","open","high","low","close","volume"]
                df["close"] = df["close"].astype(float)
                df["volume"] = df["volume"].astype(float)
                return df
            except:
                return None
    return None

async def get_klines_bybit_df(symbol: str, interval: str = "5m", limit: int = KLINE_LIMIT) -> Optional[pd.DataFrame]:
    url = EXCHANGE_APIS["bybit_kline"].format(symbol=symbol, interval=interval, limit=limit)
    async with aiohttp.ClientSession(connector=connector) as session:
        j = await safe_fetch(session, url)
        if isinstance(j, dict):
            data = j.get("result") or j.get("data")
            if isinstance(data, list) and len(data) > 0:
                rows = []
                for k in data:
                    try:
                        if isinstance(k, list):
                            close = float(k[4]) if len(k)>4 else float(k[2])
                            vol = float(k[5]) if len(k)>5 else 0.0
                        else:
                            close = float(k.get("close",0))
                            vol = float(k.get("volume",0) or 0)
                        rows.append([close, vol])
                    except:
                        pass
                if rows:
                    df = pd.DataFrame(rows, columns=["close","volume"])
                    return df
    return None

def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()

def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff().dropna()
    up = delta.clip(lower=0).rolling(period).mean()
    down = -delta.clip(upper=0).rolling(period).mean()
    rs = up / (down + 1e-9)
    return 100 - (100 / (1 + rs))

# -------------------------
# Support / Resistance detection
# -------------------------
def detect_support_resistance(series: pd.Series, lookback: int = 120, prominence: float = 0.002) -> Tuple[List[float], List[float]]:
    supports = []
    resistances = []
    try:
        n = min(len(series), lookback)
        s = series.iloc[-n:].reset_index(drop=True)
        for i in range(2, n-2):
            v = s[i]
            if v < s[i-1] and v < s[i+1] and v < s[i-2] and v < s[i+2]:
                supports.append(v)
            if v > s[i-1] and v > s[i+1] and v > s[i-2] and v > s[i+2]:
                resistances.append(v)
        def dedup(levels):
            out = []
            levels_sorted = sorted(levels)
            for lv in levels_sorted:
                if not out or abs(lv - out[-1]) / max(1e-9, out[-1]) > prominence:
                    out.append(lv)
            return out
        return dedup(supports), dedup(resistances)
    except:
        return [], []

# -------------------------
# Confidence scoring
# -------------------------
def compute_confidence(score_components: Dict[str, float]) -> float:
    weights = {
        "ema_trend": 0.25,
        "rsi": 0.20,
        "momentum": 0.20,
        "volume": 0.15,
        "spread": 0.10,
        "sources": 0.10
    }
    total = 0.0
    wsum = 0.0
    for k, w in weights.items():
        v = score_components.get(k, 0.0)
        total += v * w
        wsum += w
    if wsum == 0:
        return 0.0
    return round((total / wsum) * 100, 2)

# -------------------------
# Volume spike detection
# -------------------------
def detect_volume_spike(series_vol: pd.Series, multiplier: float = MIN_VOLUME_MULTIPLIER) -> Tuple[bool, float]:
    try:
        med = float(np.median(series_vol))
        last = float(series_vol.iloc[-1])
        if med <= 0:
            return False, 0.0
        ratio = last / med
        return (ratio >= multiplier), ratio
    except:
        return False, 0.0

# -------------------------
# Fake pump detection
# -------------------------
def detect_fake_pump(df_short: Optional[pd.DataFrame], df_long: Optional[pd.DataFrame]) -> Tuple[bool, str]:
    """
    Simple heuristics:
    - very large last candle vs median (price or volume) + huge spread across sources -> possible pump
    - short-term volume spike without follow-through in longer TF -> fake
    """
    try:
        # if no data -> cannot decide -> treat as not fake
        if df_short is None:
            return False, "no_short"
        vol = df_short["volume"].astype(float) if "volume" in df_short.columns else None
        closes = df_short["close"].astype(float)
        # price move in short TF:
        price_move = (closes.iloc[-1] / closes.iloc[0] - 1) * 100 if len(closes) >= 2 else 0.0
        vol_spike, vr = detect_volume_spike(vol, MIN_VOLUME_MULTIPLIER*1.5) if vol is not None else (False,0.0)
        # check longer TF for confirmation:
        long_mom = 0.0
        if df_long is not None:
            lc = df_long["close"].astype(float)
            long_mom = (lc.iloc[-1] / lc.iloc[0] - 1) * 100 if len(lc) >= 2 else 0.0
        # heuristics
        if abs(price_move) > 6 and vol_spike and abs(long_mom) < 1.0:
            return True, f"fake_pump_pm{round(price_move,2)}_vr{round(vr,2)}_lm{round(long_mom,2)}"
        return False, f"ok_pm{round(price_move,2)}_vr{round(vr,2)}_lm{round(long_mom,2)}"
    except:
        return False, "error_fake"

# -------------------------
# Market condition classification
# -------------------------
def classify_market_condition(series_close: pd.Series) -> str:
    try:
        returns = series_close.pct_change().dropna()
        vol = returns.rolling(14).std().dropna()
        recent_vol = vol.iloc[-1] if len(vol)>0 else 0.0
        # trend: compare ema20 vs ema50
        e20 = series_close.ewm(span=20, adjust=False).mean().iloc[-1]
        e50 = series_close.ewm(span=50, adjust=False).mean().iloc[-1]
        trend = "Trending" if e20 > e50 else "Ranging"
        if recent_vol * 100 > 1.5:
            return "High Volatility"
        return trend
    except:
        return "Unknown"

# -------------------------
# IQ Score (meta)
# -------------------------
def compute_iq_score(components: Dict[str, Any]) -> int:
    # components expected: confidence(0-100), spread_score(0-1), vol_score(0-1), sources_count
    try:
        conf = components.get("confidence", 50.0)
        spread = components.get("spread", 0.5)
        vol = components.get("volume", 0.0)
        sources = components.get("sources_count", 1)
        # simple weighted IQ
        iq = 0.5 * conf + 25 * (1.0 - spread) + 25 * vol
        # penalize low sources
        if sources < 3:
            iq *= 0.9
        return int(max(0, min(100, round(iq))))
    except:
        return 50

# -------------------------
# Multi-TF analysis (1m/5m/15m/1h) — enhanced
# -------------------------
async def multi_tf_analysis(symbol: str) -> Optional[Dict[str, Any]]:
    try:
        prices = await fetch_prices(symbol)
        price_values = [p for p in prices.values() if p is not None]
        if not price_values:
            return None
        consensus = float(np.median(price_values))
        sources_count = len(price_values)

        # fetch klines; prefer binance then bybit
        df1 = await get_klines_binance_df(symbol, "1m", 120) or await get_klines_bybit_df(symbol, "1m", 120)
        df5 = await get_klines_binance_df(symbol, "5m", 120) or await get_klines_bybit_df(symbol, "5m", 120)
        df15 = await get_klines_binance_df(symbol, "15m", 120) or await get_klines_bybit_df(symbol, "15m", 120)
        df60 = await get_klines_binance_df(symbol, "1h", 120) or await get_klines_bybit_df(symbol, "60m", 120)

        notes = []
        comp = {}

        def analyze_df(df: pd.DataFrame) -> Dict[str, Any]:
            res = {"ema_trend":0.0,"rsi":50.0,"momentum":0.0,"volume":0.0}
            try:
                closes = df["close"].astype(float)
                v = df["volume"].astype(float) if "volume" in df.columns else pd.Series([0]*len(df))
                e20 = ema(closes, 20)
                e50 = ema(closes, 50)
                trend = 1.0 if float(e20.iloc[-1]) > float(e50.iloc[-1]) else -1.0
                diff = abs(float(e20.iloc[-1]) - float(e50.iloc[-1])) / max(1e-9, float(e50.iloc[-1]))
                trend_strength = np.tanh(diff*100)
                rs = rsi(closes, 14)
                rsi_val = float(rs.iloc[-1])
                recent = closes.iloc[-12:] if len(closes) >= 12 else closes
                mom = (recent.iloc[-1] / recent.iloc[0] - 1) * 100 if len(recent) >= 2 else 0.0
                mom_norm = np.tanh(abs(mom)/5.0)
                vol_med = np.median(v) if len(v)>0 else 0
                vol_last = float(v.iloc[-1]) if len(v)>0 else 0
                vol_score = 0.0
                if vol_med > 0:
                    vol_ratio = vol_last/vol_med
                    vol_score = min(2.0, vol_ratio) - 1.0
                    vol_score = max(0.0, min(1.0, vol_score))
                res["ema_trend"] = 1.0*trend*trend_strength
                res["rsi"] = rsi_val
                res["momentum"] = mom_norm
                res["volume"] = vol_score
            except:
                pass
            return res

        tf1 = analyze_df(df1) if df1 is not None else None
        tf5 = analyze_df(df5) if df5 is not None else None
        tf15 = analyze_df(df15) if df15 is not None else None
        tf60 = analyze_df(df60) if df60 is not None else None

        ema_vals = [tf["ema_trend"] for tf in (tf1, tf5, tf15, tf60) if tf]
        if ema_vals:
            pos = sum(1 for v in ema_vals if v > 0)
            neg = sum(1 for v in ema_vals if v < 0)
            ema_trend_score = pos / len(ema_vals) if pos>=neg else 0.0
        else:
            ema_trend_score = 0.0
        comp["ema_trend"] = ema_trend_score

        rsi_scores = []
        for tf in (tf1, tf5, tf15):
            if tf:
                rv = tf["rsi"]
                if rv < 30:
                    rsi_scores.append(1.0)
                elif rv > 70:
                    rsi_scores.append(0.2)
                else:
                    rsi_scores.append(0.6)
        comp["rsi"] = float(np.mean(rsi_scores)) if rsi_scores else 0.5

        mom_vals = [tf["momentum"] for tf in (tf1, tf5, tf15, tf60) if tf]
        comp["momentum"] = float(np.mean(mom_vals)) if mom_vals else 0.0

        vol_vals = [tf["volume"] for tf in (tf1, tf5, tf15, tf60) if tf]
        comp["volume"] = float(np.mean(vol_vals)) if vol_vals else 0.0

        try:
            spread = (max(price_values) - min(price_values)) / max(1e-9, min(price_values))
            spread_score = max(0.0, 1.0 - (spread / 0.02))
            spread_score = min(1.0, spread_score)
        except:
            spread_score = 0.0
        comp["spread"] = spread_score
        comp["sources"] = min(1.0, sources_count / 6.0)
        comp["sources_count"] = sources_count

        confidence = compute_confidence(comp)

        supports, resistances = [], []
        try:
            if df60 is not None:
                supports, resistances = detect_support_resistance(df60["close"], lookback=200, prominence=0.002)
            elif df15 is not None:
                supports, resistances = detect_support_resistance(df15["close"], lookback=120, prominence=0.002)
        except:
            supports, resistances = [], []

        vol_spike = False
        vol_ratio = 0.0
        try:
            if df5 is not None:
                vs, vr = detect_volume_spike(df5["volume"], MIN_VOLUME_MULTIPLIER)
                vol_spike, vol_ratio = vs, vr
            elif df1 is not None:
                vs, vr = detect_volume_spike(df1["volume"], MIN_VOLUME_MULTIPLIER)
                vol_spike, vol_ratio = vs, vr
        except:
            vol_spike, vol_ratio = False, 0.0

        # Fake pump check
        is_fake, fake_reason = detect_fake_pump(df5, df60)

        market_condition = "Unknown"
        try:
            if df60 is not None:
                market_condition = classify_market_condition(df60["close"])
            elif df15 is not None:
                market_condition = classify_market_condition(df15["close"])
        except:
            market_condition = "Unknown"

        notes.append(f"sources:{sources_count} spread%:{round(spread*100,3)} vol_spike:{vol_spike} vr:{round(vol_ratio,2)} fake:{is_fake}")
        if tf5:
            notes.append(f"rsi5:{round(tf5['rsi'],2)} mom5:{round(tf5['momentum'],2)} vol5:{round(tf5['volume'],2)}")
        if tf15:
            notes.append(f"rsi15:{round(tf15['rsi'],2)}")

        base_score = int(min(100, max(0, confidence)))
        if MODE == "aggressive":
            base_score = max(45, base_score)
        elif MODE == "defensive":
            base_score = min(95, base_score)

        iq = compute_iq_score({"confidence": confidence, "spread": comp.get("spread",0.5), "volume": comp.get("volume",0.0), "sources_count": sources_count})

        # Determine trade type heuristics
        trade_type = "Unknown"
        try:
            # breakout if 15m momentum high and trend positive
            if (tf15 and tf15["momentum"] > 0.4) or (tf5 and tf5["momentum"] > 0.6):
                trade_type = "Breakout"
            elif (tf1 and tf1["momentum"] > 0.25) and (tf5 and tf5["volume"] > 0.4):
                trade_type = "Scalp"
            else:
                trade_type = "Swing"
        except:
            trade_type = "Swing"

        return {
            "symbol": symbol,
            "consensus": consensus,
            "prices": prices,
            "sources_count": sources_count,
            "tf1": tf1, "tf5": tf5, "tf15": tf15, "tf60": tf60,
            "components": comp,
            "confidence": confidence,
            "score": base_score,
            "notes": notes,
            "supports": supports,
            "resistances": resistances,
            "vol_spike": vol_spike,
            "vol_ratio": round(vol_ratio,2),
            "spread": round(spread*100,4),
            "is_fake": is_fake,
            "fake_reason": fake_reason,
            "market_condition": market_condition,
            "iq": iq,
            "trade_type": trade_type
        }
    except Exception:
        traceback.print_exc()
        return None

# -------------------------
# False-signal heuristics
# -------------------------
def is_market_noisy(series_close: pd.Series, threshold_pct: float = 0.8) -> bool:
    try:
        returns = series_close.pct_change().abs() * 100
        avg = returns.tail(10).mean()
        return avg >= threshold_pct
    except:
        return False

# -------------------------
# Sniper detection
# -------------------------
def detect_sniper_from_analysis(analysis: Dict[str, Any]) -> Tuple[bool, str]:
    try:
        score = analysis.get("score", 0)
        conf = analysis.get("confidence", 0.0)
        spread_pct = analysis.get("spread", 999.0)
        sources = analysis.get("sources_count", 0)
        vol_spike = analysis.get("vol_spike", False)
        is_fake = analysis.get("is_fake", False)

        # block if fake pump detected
        if is_fake:
            return False, f"blocked_fake:{analysis.get('fake_reason','')}"
        conf_threshold = CONFIDENCE_THRESHOLD if MODE=="balanced" else (60.0 if MODE=="aggressive" else 80.0)
        if spread_pct >= MAX_SPREAD_PERCENT:
            return False, f"blocked_spread_{spread_pct}"
        vol_ok = vol_spike or (analysis.get("components",{}).get("volume",0) >= 0.5)
        ok = (score >= SNIPER_SCORE_THRESHOLD) and (conf >= conf_threshold) and ( (vol_ok) or sources>=3 )
        noisy = False
        if noisy and score < 95:
            return False, "blocked_noisy"
        reason = f"score={score},conf={conf},spread%={spread_pct},vol_ok={vol_ok},sources={sources}"
        return ok, reason
    except Exception:
        return False, "error_detect"

# -------------------------
# Formatting messages
# -------------------------
def format_arabic_message(result: Dict[str, Any], vip_badge: bool=False) -> str:
    sym = esc(result["symbol"])
    price = result["consensus"]
    score = result["score"]
    conf = result["confidence"]
    notes = result["notes"]
    prices = result["prices"]
    spread = result.get("spread",0)
    supports = result.get("supports",[])
    resistances = result.get("resistances",[])
    vol_spike = result.get("vol_spike", False)
    vol_ratio = result.get("vol_ratio", 0.0)
    iq = result.get("iq", 0)
    trade_type = result.get("trade_type","Unknown")
    market_condition = result.get("market_condition","Unknown")
    is_fake = result.get("is_fake", False)

    order = ["Binance","KuCoin","Coinbase","Kraken","Bybit","OKX","Yahoo_XAU"]
    prices_lines = ""
    for ex in order:
        if ex in prices:
            v = prices[ex]
            prices_lines += f"• <b>{ex}</b>: <code>{round(v,6) if v else 'N/A'}</code>\n"

    notes_text = "\n".join(f"• {esc(n)}" for n in notes) if notes else "• لا ملاحظات"
    vip_line = "🔶 <b>إشارة VIP</b>\n" if vip_badge else ""
    sr_text = ""
    if supports:
        sr_text += "Supports: " + ", ".join(str(round(x,4)) for x in supports[:3]) + "\n"
    if resistances:
        sr_text += "Resists: " + ", ".join(str(round(x,4)) for x in resistances[:3]) + "\n"

    body = (
        f"✨ <b>ULTRA MAX++ • SNIPER</b> ✨\n"
        f"{vip_line}🎯 <b>صفقة القنّاص — {sym}</b>\n\n"
        f"💠 <b>القوة (Score):</b> <code>{score}/100</code>\n"
        f"🧠 <b>IQ Signal:</b> <code>{iq}/100</code>\n"
        f"🔒 <b>الثقة:</b> <code>{conf}%</code>\n"
        f"💱 <b>السعر (توافق):</b> <b>{round(price,6)}</b>\n"
        f"📡 <b>تباين بين منصات:</b> <code>{spread}%</code>\n"
        f"📈 <b>تأكيد حجم:</b> <code>{'Yes' if vol_spike else 'No'} ({vol_ratio}x)</code>\n"
        f"🏷️ <b>نوع الصفقة:</b> <code>{trade_type}</code>\n"
        f"🌍 <b>حالة السوق:</b> <code>{market_condition}</code>\n\n"
        f"📝 <b>ملاحظات:</b>\n{notes_text}\n\n"
        f"🔎 <b>مستويات S/R (قريب):</b>\n{sr_text}\n"
        f"🌐 <b>أسعار المنصات:</b>\n{prices_lines}\n"
        f"💰 <b>اقتراح (محاكاة):</b> {ALLOCATION_SIM}% من رأس المال\n\n"
        f"🕒 <i>الوقت:</i> <code>{now_iso()}</code>\n"
        f"⚠️ <i>تنبيه: محاكاة فقط — لا تنفيذ حقيقي</i>"
    )
    if is_fake:
        body = "⚠️ <b>ملاحظة:</b> تم اكتشاف احتمالية Fake Pump — الإشارة تم إيقافها.\n\n" + body
    return body

def format_english_message(result: Dict[str, Any], vip_badge: bool=False) -> str:
    score = result["score"]
    conf = result["confidence"]
    price = result["consensus"]
    notes = result["notes"]
    vol_spike = result.get("vol_spike", False)
    iq = result.get("iq",0)
    trade_type = result.get("trade_type","Unknown")
    market_condition = result.get("market_condition","Unknown")
    en = (
        f"✨ ULTRA MAX++ • SNIPER ✨\n"
        f"🎯 {BOT_NAME} — {result['symbol']}\n\n"
        f"<b>Score:</b> <code>{score}/100</code>  —  <b>Confidence:</b> <code>{conf}%</code>\n"
        f"<b>IQ Signal:</b> <code>{iq}/100</code>\n"
        f"<b>Consensus Price:</b> <b>{round(price,6)}</b>\n"
        f"<b>Volume Confirmed:</b> {vol_spike}\n"
        f"<b>Trade Type:</b> {trade_type}  —  <b>Market:</b> {market_condition}\n"
        f"<b>Notes:</b>\n" + "\n".join(f"• {esc(n)}" for n in notes) + "\n\n"
        f"<i>Simulation only — no real execution</i>"
    )
    return en

def format_message_dual(result: Dict[str, Any], vip_badge: bool=False) -> str:
    ar = format_arabic_message(result, vip_badge)
    en = format_english_message(result, vip_badge)
    if LANG == "ar":
        return ar
    if LANG == "en":
        return en
    return ar + "\n\n" + en

# -------------------------
# Buttons
# -------------------------
def signal_buttons(symbol: str) -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    tv_symbol = symbol.replace("USDT", "USDT").replace("XAUUSD","XAUUSD")
    kb.row(types.InlineKeyboardButton(text="📈 TradingView", url=f"https://www.tradingview.com/symbols/{tv_symbol}/"),
           types.InlineKeyboardButton(text="🔁 Refresh", callback_data=f"refresh|{symbol}"))
    kb.row(types.InlineKeyboardButton(text="📝 Details", callback_data=f"details|{symbol}"))
    return kb.as_markup()

# -------------------------
# Cooldown (smart)
# -------------------------
last_sent: Dict[str, float] = {}
last_strength: Dict[str, float] = {}

def can_send(symbol: str) -> bool:
    t = last_sent.get(symbol)
    if not t:
        return True
    elapsed = time.time() - t
    strength = last_strength.get(symbol, 0.0)
    base = COOLDOWN_SECONDS
    if strength >= 90:
        base = max(60, int(COOLDOWN_SECONDS * 0.4))
    elif strength >= 80:
        base = max(120, int(COOLDOWN_SECONDS * 0.6))
    else:
        base = COOLDOWN_SECONDS
    return elapsed >= base

def mark_sent(symbol: str, strength: float = 0.0):
    last_sent[symbol] = time.time()
    last_strength[symbol] = strength

# -------------------------
# Sound
# -------------------------
async def try_send_sound(chat_id: int):
    if not ALERT_SOUND_ENABLED:
        return
    if not os.path.exists(ALERT_SOUND_FILE):
        return
    try:
        with open(ALERT_SOUND_FILE, "rb") as f:
            await bot.send_audio(chat_id, f)
    except Exception:
        traceback.print_exc()

# -------------------------
# SEND: PRIVATE ONLY
# -------------------------
async def send_signal_to_channels(message_text: str, vip_badge: bool, payload: dict):
    """
    PRIVATE-only sending: sends message to CHAT_ID (your user id).
    VIP/Channel sending disabled by default for privacy.
    """
    try:
        dest = int(CHAT_ID)  # must be your numeric user id
        await bot.send_message(dest, message_text, reply_markup=signal_buttons(payload.get("symbol","")), disable_notification=False)
        db_insert_signal(int(time.time()), payload.get("symbol",""), payload.get("score",0), payload.get("confidence",0.0), payload.get("reason",""), payload, vip=False)
        # auto-learn store
        if AUTO_LEARN:
            db_learn_insert(int(time.time()), payload.get("symbol",""), payload.get("score",0), payload.get("confidence",0.0), outcome="sent")
    except Exception:
        traceback.print_exc()

# -------------------------
# Worker & monitor: improved Top-K selection
# -------------------------
async def analyze_all_symbols(symbols: List[str]) -> List[Dict[str, Any]]:
    # Map symbol -> analysis concurrently with limited workers
    sem = asyncio.Semaphore(min(20, len(symbols)))
    results = []

    async def worker(sym):
        async with sem:
            try:
                a = await multi_tf_analysis(sym)
                if a:
                    results.append(a)
            except:
                traceback.print_exc()

    tasks = [asyncio.create_task(worker(s)) for s in symbols]
    await asyncio.gather(*tasks, return_exceptions=True)
    return results

async def monitor_loop():
    try:
        await bot.send_message(int(CHAT_ID), f"🚀 <b>{BOT_NAME}</b> — Final online (analysis-only). MODE: {MODE}  Speed: {'ON' if SPEED_MODE else 'OFF'}", disable_notification=True)
    except:
        pass

    while True:
        try:
            analyses = await analyze_all_symbols(GOLDEN_LIST)
            if not analyses:
                await asyncio.sleep(MONITOR_INTERVAL)
                continue
            # filter out fakes and by heuristics
            valid = []
            for a in analyses:
                ok, reason = detect_sniper_from_analysis(a)
                a["_allowed"] = ok
                a["_reason"] = reason
                valid.append(a)
            # choose top-K by (score, confidence, iq)
            sorted_by = sorted(valid, key=lambda x: (x.get("score",0), x.get("confidence",0), x.get("iq",0)), reverse=True)
            top = sorted_by[:TOP_K]
            for analysis in top:
                if analysis.get("_allowed"):
                    sym = analysis["symbol"]
                    if can_send(sym):
                        try:
                            vip_badge = False
                            body = format_message_dual(analysis, vip_badge=vip_badge)
                            payload = {"symbol": analysis["symbol"], "score": analysis["score"], "confidence": analysis["confidence"], "reason": analysis.get("_reason",""), "analysis": analysis}
                            await send_signal_to_channels(body, vip_badge, payload)
                            await try_send_sound(int(CHAT_ID))
                            mark_sent(sym, analysis.get("score",0))
                            append_json(SIGNALS_LOG, {"time": int(time.time()), "symbol": sym, "score": analysis["score"], "confidence": analysis["confidence"], "reason": analysis.get("_reason",""), "vip": vip_badge})
                        except Exception:
                            traceback.print_exc()
                else:
                    # optionally log blocked reasons
                    append_json(CONV_LOG, {"time": int(time.time()), "symbol": analysis.get("symbol"), "blocked": True, "reason": analysis.get("_reason","")})
            await asyncio.sleep(MONITOR_INTERVAL)
        except Exception:
            traceback.print_exc()
            await asyncio.sleep(MONITOR_INTERVAL)

# -------------------------
# Web Dashboard (kept for local control)
# -------------------------
DASH_TEMPLATE = """
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>Ultra Sniper Pro Max++ Final — Dashboard (Private)</title>
<style>
body{font-family: Arial, Helvetica, sans-serif; background:#071427; color:#f7edd1; padding:20px}
.card{background:#0f1720;padding:18px;border-radius:10px;margin-top:12px}
pre {white-space:pre-wrap;word-break:break-word}
button{background:#ffd700;border:none;padding:6px 10px;border-radius:6px}
select, input {padding:6px;border-radius:6px;border:none}
.small{font-size:0.9em;color:#cbd5e1}
</style>
</head>
<body>
<div class="card">
  <h2>Ultra Sniper Pro Max++ Final — Dashboard (Private)</h2>
  <div class="small">Status: <b>running</b> — Speed: <b>{{ speed }}</b></div>
  <h3>Settings</h3>
  <pre>{{ settings_json }}</pre>
  <form method="POST" action="/set-lang">
    <label>Language:</label>
    <select name="lang">
      <option value="both" {% if lang=='both' %}selected{% endif %}>Arabic + English</option>
      <option value="ar" {% if lang=='ar' %}selected{% endif %}>Arabic</option>
      <option value="en" {% if lang=='en' %}selected{% endif %}>English</option>
    </select>
    <button>Save</button>
  </form>
  <form method="GET" action="/export" style="margin-top:8px">
    <button>Export CSV (signals)</button>
  </form>
</div>

<div class="card">
  <h3>Recent Signals (latest 50)</h3>
  {% for s in signals %}
    <div style="padding:8px;border-left:4px solid #ffd700;margin:8px 0;background:#071427;">
      <b>{{ s.symbol }}</b> — score: <code>{{ s.score }}</code> — confidence: <code>{{ s.confidence }}</code> — time: {{ s.time_iso }} <br/>
      reason: {{ s.reason }} <br/>
    </div>
  {% else %}
    <div>No signals yet.</div>
  {% endfor %}
</div>

</body>
</html>
"""

async def dashboard_handler(request):
    settings = SETTINGS.copy()
    signals = db_get_recent(50)
    tpl = Template(DASH_TEMPLATE)
    html = tpl.render(settings_json=json.dumps(settings, ensure_ascii=False, indent=2), signals=signals, lang=LANG, speed=("ON" if SPEED_MODE else "OFF"))
    return web.Response(text=html, content_type='text/html')

async def set_lang_handler(request):
    global LANG, SETTINGS
    data = await request.post()
    lang = data.get("lang", "both")
    if lang in ("both","ar","en"):
        SETTINGS["LANG"] = lang
        LANG = lang
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(SETTINGS, f, ensure_ascii=False, indent=2)
    raise web.HTTPFound('/')

async def export_handler(request):
    path = db_export_csv()
    return web.FileResponse(path)

# -------------------------
# Bot commands: catch-all + test
# -------------------------
@dp.message()
async def catch_all(m: types.Message):
    try:
        if m.text and m.text.strip().lower().startswith("test "):
            # handle test command
            parts = m.text.strip().split()
            if len(parts) >= 2:
                sym = parts[1].upper()
                await m.reply(f"🔎 Running test for {sym} ...")
                analysis = await multi_tf_analysis(sym)
                if not analysis:
                    await m.reply(f"⚠️ لا توجد بيانات كافية لـ {sym}.")
                    return
                # build detailed test report
                is_fake = analysis.get("is_fake", False)
                iq = analysis.get("iq", 0)
                trade_type = analysis.get("trade_type","Unknown")
                market_cond = analysis.get("market_condition","Unknown")
                score = analysis.get("score",0)
                conf = analysis.get("confidence",0.0)
                spread = analysis.get("spread",0.0)
                vol = analysis.get("vol_ratio",0.0)
                notes = analysis.get("notes",[])
                badge = "🔰" if score >= 90 and conf >= 85 and iq >= 85 else ""
                report = (
                    f"🧾 <b>TEST REPORT — {sym}</b>\n\n"
                    f"⛔ Fake Pump?: <b>{'Yes' if is_fake else 'No'}</b>\n"
                    f"📉 Market: <b>{market_cond}</b>\n"
                    f"📊 Trade Type: <b>{trade_type}</b>\n"
                    f"💹 Spread: <code>{spread}%</code>\n"
                    f"📈 Volume ratio: <code>{vol}x</code>\n"
                    f"🧠 IQ Score: <code>{iq}/100</code>\n"
                    f"🧮 Sniper Score: <code>{score}/100</code>\n"
                    f"🔰 Confidence: <code>{conf}%</code>\n\n"
                    f"💡 Recommendation: {'✅ Strong (consider)' if score>=SNIPER_SCORE_THRESHOLD and conf>=CONFIDENCE_THRESHOLD and not is_fake else '⛔ Skip / Monitor'} {badge}\n\n"
                    f"📝 Notes:\n" + "\n".join(f"• {esc(n)}" for n in notes)
                )
                await m.reply(report)
                return
        # generic reply to owner
        if str(m.from_user.id) == str(CHAT_ID) or str(m.chat.id) == str(CHAT_ID):
            text = (
                f"🎯 <b>{BOT_NAME}</b> يعمل تلقائيًا.\n"
                f"🕒 الوقت: <code>{now_iso()}</code>\n"
                f"لوحة التحكم: http://<host>:{os.getenv('PORT','8080')}/"
            )
            await m.reply(text)
    except Exception:
        traceback.print_exc()

@dp.callback_query()
async def callbacks(c: types.CallbackQuery):
    try:
        data = c.data or ""
        if data.startswith("refresh|"):
            _, sym = data.split("|",1)
            analysis = await multi_tf_analysis(sym)
            if analysis:
                body = format_message_dual(analysis, vip_badge=False)
                await bot.send_message(c.message.chat.id, body, reply_markup=signal_buttons(sym))
            await c.answer("تم التحديث.")
        elif data.startswith("details|"):
            _, sym = data.split("|",1)
            analysis = await multi_tf_analysis(sym)
            if analysis:
                txt = json.dumps(analysis, ensure_ascii=False, indent=2)
                await bot.send_message(c.message.chat.id, f"<pre>{esc(txt)}</pre>")
            await c.answer("تم إظهار التفاصيل.")
        else:
            await c.answer("تم.")
    except Exception:
        traceback.print_exc()

# -------------------------
# start web app + main
# -------------------------
async def start_web_app():
    app = web.Application()
    app.router.add_get('/', dashboard_handler)
    app.router.add_post('/set-lang', set_lang_handler)
    app.router.add_get('/export', export_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', int(os.getenv("PORT", "8080")))
    await site.start()

async def main():
    global connector
    connector = aiohttp.TCPConnector(limit=HTTP_CONN_LIMIT, ttl_dns_cache=300)

    asyncio.create_task(start_web_app())
    asyncio.create_task(monitor_loop())

    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        import uvloop
        uvloop.install()
    except:
        pass
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(main())
    finally:
        loop.close()
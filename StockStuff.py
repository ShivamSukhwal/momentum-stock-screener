from flask import Flask, request, jsonify
import requests, os, datetime, time
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
if not POLYGON_API_KEY:
    raise ValueError("POLYGON_API_KEY not found in environment variables. Please check your .env file.")
POLY = "https://api.polygon.io"

def http_get(path, params=None, timeout=10):
    params = params or {}
    params["apiKey"] = POLYGON_API_KEY
    r = requests.get(f"{POLY}{path}", params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def normalize_prev_close(ticker: str):
    data = http_get(f"/v2/aggs/ticker/{ticker}/prev")
    out = {"ticker": ticker, "status": data.get("status")}
    if data.get("results"):
        r0 = data["results"][0]
        ts = int(r0.get("t", 0)) // 1000
        out.update({
            "open":  r0.get("o"),
            "high":  r0.get("h"),
            "low":   r0.get("l"),
            "close": r0.get("c"),
            "volume": r0.get("v"),
            "epoch": ts,
            "date": datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d"),
        })
    return out

def last_trade(ticker: str):
    # Get latest trade data (Stock Advanced plan)
    data = http_get(f"/v2/last/trade/{ticker}")
    p = data.get("results", {})
    price = p.get("p")
    ts = p.get("t")
    if ts and ts > 1e12:  # ns/us to ms
        # Normalize to seconds
        while ts > 1e12:
            ts //= 1000
    return {
        "last_price": price,
        "last_trade_ts": int(ts) if ts else None,
        "last_trade_iso": datetime.datetime.utcfromtimestamp(int(ts)).isoformat()+"Z" if ts else None
    }

def real_time_quote(ticker: str):
    # Get real-time quote (bid/ask, last price) - Stock Advanced feature
    data = http_get(f"/v2/last/nbbo/{ticker}")
    results = data.get("results", {})
    return {
        "bid": results.get("P"),  # Bid price
        "bid_size": results.get("S"),  # Bid size
        "ask": results.get("p"),  # Ask price  
        "ask_size": results.get("s"),  # Ask size
        "spread": (results.get("p", 0) - results.get("P", 0)) if results.get("p") and results.get("P") else None,
        "quote_timestamp": results.get("t")
    }

def get_daily_bars(ticker: str, days: int = 30):
    # Get daily OHLCV data for trend analysis
    end_date = datetime.datetime.utcnow()
    start_date = end_date - datetime.timedelta(days=days)
    
    data = http_get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}",
        params={"adjusted": "true", "sort": "desc", "limit": days}
    )
    
    bars = data.get("results", [])
    if not bars:
        return {}
    
    # Calculate technical indicators
    closes = [bar.get("c", 0) for bar in bars if bar.get("c")]
    volumes = [bar.get("v", 0) for bar in bars if bar.get("v")]
    
    if len(closes) >= 5:
        sma_5 = sum(closes[:5]) / 5
        sma_20 = sum(closes[:20]) / 20 if len(closes) >= 20 else None
        
        # Price momentum (5-day change)
        momentum_5d = ((closes[0] - closes[4]) / closes[4] * 100) if len(closes) > 4 else 0
        
        return {
            "sma_5": sma_5,
            "sma_20": sma_20,
            "momentum_5d": momentum_5d,
            "avg_volume_30d": sum(volumes) / len(volumes) if volumes else 0,
            "recent_high": max(bar.get("h", 0) for bar in bars[:5]),
            "recent_low": min(bar.get("l", 0) for bar in bars[:5])
        }
    
    return {}

def today_intraday_volume(ticker: str):
    # Sum today's minute volumes
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    data = http_get(f"/v2/aggs/ticker/{ticker}/range/1/minute/{today}/{today}", params={"adjusted": "true", "limit": 50000})
    total = 0
    for bar in data.get("results", []) or []:
        v = bar.get("v") or 0
        total += v
    return int(total)

def avg_volume_days(ticker: str, days=10):
    # Average of last N *completed* daily bars (excludes today)
    end = datetime.datetime.utcnow().date() - datetime.timedelta(days=1)
    start = end - datetime.timedelta(days=days*2)  # buffer for weekends/holidays
    data = http_get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}",
        params={"adjusted": "true", "sort": "desc", "limit": days*2}
    )
    vols = []
    for bar in data.get("results", []) or []:
        if len(vols) >= days:
            break
        vols.append(bar.get("v") or 0)
    return (sum(vols) / len(vols)) if vols else None

def metrics_for(ticker: str):
    base = {"ticker": ticker}
    try:
        # Get previous close data
        prev = normalize_prev_close(ticker)
        base["prev"] = prev
        
        # Get real-time trade data (Stock Advanced)
        lt = last_trade(ticker)
        base.update({
            "last_price": lt.get("last_price"),
            "last_trade_ts": lt.get("last_trade_ts"),
            "last_trade_iso": lt.get("last_trade_iso"),
        })
        
        # Get real-time quote data (bid/ask)
        quote = real_time_quote(ticker)
        base["quote"] = quote
        
        # Calculate price change metrics
        prev_close = prev.get("close")
        current_price = lt.get("last_price")
        if prev_close and current_price:
            price_change = current_price - prev_close
            price_change_pct = (price_change / prev_close) * 100
            base.update({
                "price_change": price_change,
                "price_change_pct": price_change_pct
            })
        
        # Get volume data
        tv = today_intraday_volume(ticker)
        av10 = avg_volume_days(ticker, days=10)
        rvol = (tv / av10) if (av10 and av10 > 0) else None
        base.update({
            "today_volume": tv,
            "avg_volume_10d": av10,
            "rvol": rvol
        })
        
        # Get technical analysis data
        technical = get_daily_bars(ticker)
        base["technical"] = technical
        
        # Calculate momentum score (0-100)
        momentum_score = 50  # Base score
        if base.get("price_change_pct", 0) > 5:
            momentum_score += 20
        if base.get("rvol", 0) > 3:
            momentum_score += 15
        if technical.get("momentum_5d", 0) > 10:
            momentum_score += 15
        
        base["momentum_score"] = min(100, momentum_score)
            
    except requests.exceptions.RequestException as e:
        base["error"] = str(e)
    return base

@app.route("/")
def index():
    return jsonify({
        "message": "Momentum Stock Screener API - Stock Advanced Plan",
        "version": "2.0",
        "features": [
            "Real-time quotes and trades",
            "Bid/Ask spreads", 
            "Technical indicators",
            "Momentum scoring",
            "Volume analysis"
        ],
        "endpoints": {
            "health": "/health",
            "basic_stock": "/stock?ticker=AAPL",
            "real_time_quote": "/quote?ticker=AAPL", 
            "full_metrics": "/metrics?ticker=AAPL",
            "multiple_stocks": "/stocks?tickers=AAPL,TSLA,AMD",
            "multiple_with_metrics": "/stocks?tickers=AAPL,TSLA&metrics=1"
        }
    })

@app.route("/health")
def health():
    return {"status": "ok"}, 200

@app.route("/stock")
def stock():
    t = (request.args.get("ticker") or "AAPL").upper()
    try:
        return jsonify(normalize_prev_close(t)), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"ticker": t, "error": str(e)}), 502

@app.route("/metrics")
def metrics():
    t = (request.args.get("ticker") or "AAPL").upper()
    return jsonify(metrics_for(t)), 200

@app.route("/quote")
def quote():
    t = (request.args.get("ticker") or "AAPL").upper()
    try:
        quote_data = real_time_quote(t)
        trade_data = last_trade(t)
        prev_data = normalize_prev_close(t)
        
        result = {
            "ticker": t,
            "bid": quote_data.get("bid"),
            "ask": quote_data.get("ask"),
            "spread": quote_data.get("spread"),
            "last_price": trade_data.get("last_price"),
            "prev_close": prev_data.get("close"),
            "timestamp": trade_data.get("last_trade_iso")
        }
        
        # Calculate change
        if result["last_price"] and result["prev_close"]:
            change = result["last_price"] - result["prev_close"]
            change_pct = (change / result["prev_close"]) * 100
            result.update({
                "change": change,
                "change_pct": change_pct
            })
            
        return jsonify(result), 200
    except requests.exceptions.RequestException as e:
        return jsonify({"ticker": t, "error": str(e)}), 502

@app.route("/stocks")
def stocks():
    raw = request.args.get("tickers", "")
    tickers = [t.strip().upper() for t in raw.split(",") if t.strip()]
    want_metrics = (request.args.get("metrics") == "1")
    if not tickers:
        return jsonify({"error": "Provide ?tickers=AAPL,TSLA,AMD"}), 400
    results = {}
    for t in tickers:
        try:
            results[t] = metrics_for(t) if want_metrics else normalize_prev_close(t)
        except requests.exceptions.RequestException as e:
            results[t] = {"ticker": t, "error": str(e)}
    return jsonify(results), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

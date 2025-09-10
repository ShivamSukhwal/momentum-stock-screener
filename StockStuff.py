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
    # Try to get latest trade, fallback to previous close if forbidden
    try:
        data = http_get(f"/v2/last/trade/{ticker}")
        p = data.get("results", {}) or data  # Polygon responses vary by plan; fall back
        price = p.get("p") or p.get("price")
        ts = p.get("t") or p.get("sip_timestamp") or p.get("participant_timestamp")
        if ts and ts > 1e12:  # ns/us to ms
            # Normalize to seconds
            while ts > 1e12:
                ts //= 1000
        return {
            "last_price": price,
            "last_trade_ts": int(ts) if ts else None,
            "last_trade_iso": datetime.datetime.utcfromtimestamp(int(ts)).isoformat()+"Z" if ts else None
        }
    except requests.exceptions.HTTPError as e:
        if "403" in str(e) or "Forbidden" in str(e):
            # Fallback to previous close data (available on free plans)
            prev_data = normalize_prev_close(ticker)
            if prev_data.get("close"):
                return {
                    "last_price": prev_data["close"],
                    "last_trade_ts": prev_data.get("epoch"),
                    "last_trade_iso": prev_data.get("date") + "T16:00:00Z" if prev_data.get("date") else None
                }
        raise e

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
        prev = normalize_prev_close(ticker)
        base["prev"] = prev
        
        # Try to get latest trade data, fallback gracefully
        try:
            lt = last_trade(ticker)
            base.update({
                "last_price": lt.get("last_price"),
                "last_trade_ts": lt.get("last_trade_ts"),
                "last_trade_iso": lt.get("last_trade_iso"),
            })
        except requests.exceptions.RequestException:
            # Use previous close as last price if real-time data unavailable
            base.update({
                "last_price": prev.get("close"),
                "last_trade_ts": prev.get("epoch"),
                "last_trade_iso": prev.get("date") + "T16:00:00Z" if prev.get("date") else None,
            })
        
        # Try to get volume data
        try:
            tv = today_intraday_volume(ticker)
            av10 = avg_volume_days(ticker, days=10)
            rvol = (tv / av10) if (av10 and av10 > 0) else None
            base.update({
                "today_volume": tv,
                "avg_volume_10d": av10,
                "rvol": rvol
            })
        except requests.exceptions.RequestException:
            # Use previous day volume if intraday not available
            base.update({
                "today_volume": prev.get("volume", 0),
                "avg_volume_10d": prev.get("volume", 0),
                "rvol": 1.0  # Default relative volume
            })
            
    except requests.exceptions.RequestException as e:
        base["error"] = str(e)
    return base

@app.route("/")
def index():
    return jsonify({
        "message": "Polygon proxy is running.",
        "try": [
            "/health",
            "/stock?ticker=AAPL",
            "/stocks?tickers=AAPL,TSLA,AMD",
            "/metrics?ticker=AAPL",
            "/stocks?tickers=AAPL,TSLA&metrics=1"
        ]
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

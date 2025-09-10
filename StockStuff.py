from flask import Flask, request, jsonify
from flask_cors import CORS
import requests, os, datetime, time, json, sqlite3
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Create logs directory if it doesn't exist
LOGS_DIR = "scanner_logs"
BACKUP_DIR = "scanner_backups"
DB_PATH = "scanner_data.db"

if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)
if not os.path.exists(BACKUP_DIR):
    os.makedirs(BACKUP_DIR)

# Initialize SQLite database for permanent storage
def init_database():
    """Initialize SQLite database for permanent data storage"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Create main hits table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scanner_hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hit_id INTEGER,
            date TEXT,
            timestamp TEXT,
            time_readable TEXT,
            market_session TEXT,
            ticker TEXT,
            price REAL,
            price_change_pct REAL,
            price_category TEXT,
            volume INTEGER,
            relative_volume REAL,
            volume_category TEXT,
            momentum_score INTEGER,
            primary_trigger TEXT,
            trigger_description TEXT,
            breakout_detected INTEGER,
            news_detected INTEGER,
            signal_strength INTEGER,
            risk_level TEXT,
            scanner_criteria TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Create daily summaries table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_summaries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT UNIQUE,
            total_hits INTEGER,
            unique_tickers INTEGER,
            trigger_types TEXT,  -- JSON string
            price_ranges TEXT,   -- JSON string
            performance_metrics TEXT,  -- JSON string
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()

# Initialize database on startup
init_database()

POLYGON_API_KEY = os.getenv("POLYGON_API_KEY")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY")
CLOUD_BACKUP_URL = os.getenv("CLOUD_BACKUP_URL")  # Optional: webhook URL for cloud backups
if not POLYGON_API_KEY:
    raise ValueError("POLYGON_API_KEY not found in environment variables. Please check your .env file.")
if not FINNHUB_API_KEY:
    raise ValueError("FINNHUB_API_KEY not found in environment variables. Please check your .env file.")
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
            "multiple_with_metrics": "/stocks?tickers=AAPL,TSLA&metrics=1",
            "market_news": "/news",
            "scanner_log": "/log-hit (POST)",
            "daily_summary": "/daily-summary?date=2024-01-01",
            "archive_logs": "/archive-logs (POST)"
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

def get_finnhub_news():
    """Get general market news from Finnhub"""
    try:
        url = "https://finnhub.io/api/v1/news"
        params = {
            'category': 'general',
            'token': FINNHUB_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        print(f"Finnhub news error: {e}")
        return []

@app.route("/news")
def news():
    """Get latest stock market news"""
    try:
        news_data = get_finnhub_news()
        
        # Format news for frontend
        formatted_news = []
        for item in news_data[:15]:  # Limit to 15 articles
            formatted_news.append({
                "headline": item.get("headline", ""),
                "summary": item.get("summary", "")[:200] + "..." if len(item.get("summary", "")) > 200 else item.get("summary", ""),
                "source": item.get("source", "Unknown"),
                "datetime": datetime.datetime.fromtimestamp(item.get("datetime", 0)).isoformat() if item.get("datetime") else datetime.datetime.now().isoformat(),
                "url": item.get("url", ""),
                "image": item.get("image", "")
            })
        
        return jsonify(formatted_news), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/breakout")
def breakout():
    """Get rapid breakout data for a ticker (minute-level price analysis)"""
    t = (request.args.get("ticker") or "AAPL").upper()
    try:
        # Get last 5 minutes of data for breakout detection
        end_time = datetime.datetime.utcnow()
        start_time = end_time - datetime.timedelta(minutes=5)
        
        # Get minute-level bars (Stock Advanced plan feature)
        data = http_get(
            f"/v2/aggs/ticker/{t}/range/1/minute/{start_time.strftime('%Y-%m-%d')}/{end_time.strftime('%Y-%m-%d')}",
            params={
                "adjusted": "true",
                "sort": "desc",
                "limit": 10,
                "timestamp.gte": int(start_time.timestamp() * 1000),
                "timestamp.lte": int(end_time.timestamp() * 1000)
            }
        )
        
        bars = data.get("results", [])
        if len(bars) < 2:
            return jsonify({"ticker": t, "breakout_detected": False, "reason": "insufficient_data"}), 200
        
        # Analyze for rapid price movement (5%+ in last minute)
        latest_bar = bars[0]
        previous_bar = bars[1] if len(bars) > 1 else bars[0]
        
        latest_close = latest_bar.get("c", 0)
        previous_close = previous_bar.get("c", 0)
        
        if previous_close > 0:
            minute_change_pct = ((latest_close - previous_close) / previous_close) * 100
        else:
            minute_change_pct = 0
        
        # Check for volume spike
        latest_volume = latest_bar.get("v", 0)
        avg_volume = sum(bar.get("v", 0) for bar in bars) / len(bars)
        volume_spike = (latest_volume / avg_volume) if avg_volume > 0 else 1
        
        # Breakout criteria: 5%+ move in last minute OR massive volume spike
        breakout_detected = abs(minute_change_pct) >= 5.0 or volume_spike >= 10
        
        return jsonify({
            "ticker": t,
            "breakout_detected": breakout_detected,
            "minute_change_pct": minute_change_pct,
            "latest_price": latest_close,
            "previous_price": previous_close,
            "volume_spike": volume_spike,
            "latest_volume": latest_volume,
            "bars_analyzed": len(bars),
            "timestamp": end_time.isoformat()
        }), 200
        
    except requests.exceptions.RequestException as e:
        return jsonify({"ticker": t, "error": str(e)}), 502

@app.route("/recent-news")
def recent_news():
    """Get very recent market news (last 2 hours)"""
    try:
        # Get recent general market news
        url = "https://finnhub.io/api/v1/news"
        params = {
            'category': 'general',
            'token': FINNHUB_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        news_data = response.json()
        
        # Filter to very recent news (last 2 hours)
        two_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=2)
        recent_news = []
        
        for item in news_data[:50]:  # Check recent articles
            news_time = datetime.datetime.fromtimestamp(item.get("datetime", 0))
            if news_time > two_hours_ago:
                recent_news.append({
                    "headline": item.get("headline", ""),
                    "summary": item.get("summary", "")[:150] + "..." if len(item.get("summary", "")) > 150 else item.get("summary", ""),
                    "source": item.get("source", ""),
                    "datetime": news_time.isoformat(),
                    "url": item.get("url", ""),
                    "related_symbols": extract_symbols_from_text(item.get("headline", "") + " " + item.get("summary", ""))
                })
        
        return jsonify(recent_news), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def extract_symbols_from_text(text):
    """Extract potential stock symbols from news text"""
    import re
    # Look for patterns like $AAPL or common stock symbols
    symbols = re.findall(r'\$([A-Z]{2,5})', text.upper())
    # Also look for standalone 2-5 letter caps that might be symbols
    words = re.findall(r'\b([A-Z]{2,5})\b', text.upper())
    
    # Common stock symbols that appear in news
    common_symbols = ['AAPL', 'TSLA', 'NVDA', 'AMD', 'GOOGL', 'MSFT', 'META', 'AMZN', 'NFLX', 'PLTR', 'SOFI', 'RIOT', 'MARA']
    
    all_symbols = list(set(symbols + [w for w in words if w in common_symbols]))
    return all_symbols[:5]  # Limit to 5 symbols

def log_scanner_hit(ticker, trigger_data):
    """Log a scanner hit to today's log file with ChatGPT-friendly format"""
    try:
        now = datetime.datetime.now()
        today = now.strftime("%Y-%m-%d")
        log_file = os.path.join(LOGS_DIR, f"scanner_hits_{today}.json")
        
        # Load existing logs for today
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                logs = json.load(f)
        else:
            logs = {
                "log_metadata": {
                    "date": today,
                    "log_version": "2.0",
                    "purpose": "Stock scanner trigger logs for ChatGPT analysis",
                    "scanner_type": "breakout_and_news_momentum",
                    "created_at": now.isoformat(),
                    "timezone": "UTC",
                    "criteria": {
                        "price_range": "$2-$20",
                        "triggers": ["5%+ moves in <1 minute", "breaking news mentions", "10x+ volume spikes"],
                        "scan_frequency": "every 30 seconds"
                    }
                },
                "daily_summary": {
                    "total_hits": 0,
                    "unique_tickers": [],
                    "trigger_types": {
                        "minute_breakout": 0,
                        "breaking_news": 0,
                        "volume_spike": 0,
                        "breakout_and_news": 0
                    },
                    "price_ranges": {
                        "under_5": 0,
                        "5_to_10": 0,
                        "10_to_15": 0,
                        "15_to_20": 0
                    },
                    "performance_metrics": {
                        "avg_change_pct": 0,
                        "max_change_pct": 0,
                        "avg_volume_spike": 0,
                        "max_volume_spike": 0
                    }
                },
                "scanner_hits": []
            }
        
        # Create detailed hit entry
        price = trigger_data.get("price", 0)
        change_pct = trigger_data.get("change_pct", 0)
        rel_volume = trigger_data.get("rel_volume", 1)
        trigger_type = trigger_data.get("trigger_type", "unknown")
        
        hit_entry = {
            "hit_id": len(logs["scanner_hits"]) + 1,
            "timestamp": now.isoformat(),
            "time_readable": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "market_session": get_market_session(now),
            "stock_data": {
                "ticker": ticker.upper(),
                "price": round(price, 2) if price else None,
                "price_change_pct": round(change_pct, 2) if change_pct else None,
                "price_category": categorize_price(price),
                "volume": trigger_data.get("volume"),
                "relative_volume": round(rel_volume, 2) if rel_volume else None,
                "volume_category": categorize_volume_spike(rel_volume),
                "momentum_score": trigger_data.get("momentum_score")
            },
            "trigger_analysis": {
                "primary_trigger": trigger_type,
                "trigger_description": get_trigger_description(trigger_type),
                "breakout_detected": trigger_data.get("breakout_detected", False),
                "news_detected": trigger_data.get("news_detected", False),
                "signal_strength": calculate_signal_strength(trigger_data),
                "risk_level": assess_risk_level(price, change_pct, rel_volume)
            },
            "context": {
                "scanner_criteria": "$2-$20 range, 5%+ minute moves OR breaking news, 10x+ volume",
                "market_conditions": "Real-time breakout and news scanner",
                "scan_frequency": "Every 30 seconds"
            }
        }
        
        # Update summary statistics
        logs["scanner_hits"].append(hit_entry)
        update_daily_summary(logs, hit_entry)
        
        # Save to JSON file
        with open(log_file, 'w') as f:
            json.dump(logs, f, indent=2)
        
        # Also save to permanent database
        save_hit_to_database(hit_entry)
        
        return True
    except Exception as e:
        print(f"Error logging scanner hit: {e}")
        return False

def get_market_session(dt):
    """Determine market session for context"""
    hour = dt.hour
    if 9 <= hour < 16:
        return "regular_hours"
    elif 4 <= hour < 9:
        return "pre_market"
    elif 16 <= hour <= 20:
        return "after_hours"
    else:
        return "overnight"

def categorize_price(price):
    """Categorize price for analysis"""
    if not price:
        return "unknown"
    if price < 5:
        return "penny_stock"
    elif price < 10:
        return "low_priced"
    elif price < 15:
        return "mid_priced"
    else:
        return "higher_priced"

def categorize_volume_spike(rel_volume):
    """Categorize volume spike intensity"""
    if not rel_volume:
        return "normal"
    if rel_volume >= 50:
        return "extreme_spike"
    elif rel_volume >= 20:
        return "massive_spike"
    elif rel_volume >= 10:
        return "high_spike"
    elif rel_volume >= 5:
        return "moderate_spike"
    else:
        return "normal_volume"

def get_trigger_description(trigger_type):
    """Human-readable trigger descriptions"""
    descriptions = {
        "minute_breakout": "5%+ price move within 1 minute timeframe",
        "breaking_news": "Recent news mention in last 2 hours",
        "volume_spike": "10x+ volume explosion without news/breakout",
        "breakout_and_news": "Both rapid price move AND breaking news"
    }
    return descriptions.get(trigger_type, "Unknown trigger type")

def calculate_signal_strength(trigger_data):
    """Calculate signal strength 1-10 for ChatGPT analysis"""
    score = 5  # Base score
    
    change_pct = abs(trigger_data.get("change_pct", 0))
    rel_volume = trigger_data.get("rel_volume", 1)
    
    # Price movement scoring
    if change_pct >= 15:
        score += 2
    elif change_pct >= 10:
        score += 1
    
    # Volume scoring
    if rel_volume >= 50:
        score += 2
    elif rel_volume >= 20:
        score += 1
    
    # Combined trigger bonus
    if trigger_data.get("breakout_detected") and trigger_data.get("news_detected"):
        score += 1
    
    return min(10, max(1, score))

def assess_risk_level(price, change_pct, rel_volume):
    """Assess risk level for trading context"""
    if not price:
        return "unknown"
    
    # Higher risk for penny stocks with extreme moves
    if price < 5 and abs(change_pct or 0) > 20:
        return "very_high"
    elif price < 5 or abs(change_pct or 0) > 15:
        return "high"
    elif abs(change_pct or 0) > 10 or (rel_volume or 1) > 25:
        return "moderate"
    else:
        return "low"

def update_daily_summary(logs, hit_entry):
    """Update daily summary statistics"""
    summary = logs["daily_summary"]
    stock_data = hit_entry["stock_data"]
    trigger_data = hit_entry["trigger_analysis"]
    
    # Basic counts
    summary["total_hits"] += 1
    
    ticker = stock_data["ticker"]
    if ticker not in summary["unique_tickers"]:
        summary["unique_tickers"].append(ticker)
    
    # Trigger type counts
    trigger_type = trigger_data["primary_trigger"]
    if trigger_type in summary["trigger_types"]:
        summary["trigger_types"][trigger_type] += 1
    
    # Price range counts
    price = stock_data["price"]
    if price:
        if price < 5:
            summary["price_ranges"]["under_5"] += 1
        elif price < 10:
            summary["price_ranges"]["5_to_10"] += 1
        elif price < 15:
            summary["price_ranges"]["10_to_15"] += 1
        else:
            summary["price_ranges"]["15_to_20"] += 1
    
    # Performance metrics
    change_pct = stock_data["price_change_pct"]
    rel_volume = stock_data["relative_volume"]
    
    if change_pct is not None:
        # Update averages (simplified)
        current_avg = summary["performance_metrics"]["avg_change_pct"]
        summary["performance_metrics"]["avg_change_pct"] = (current_avg + abs(change_pct)) / 2
        summary["performance_metrics"]["max_change_pct"] = max(
            summary["performance_metrics"]["max_change_pct"], abs(change_pct)
        )
    
    if rel_volume is not None:
        current_avg = summary["performance_metrics"]["avg_volume_spike"]
        summary["performance_metrics"]["avg_volume_spike"] = (current_avg + rel_volume) / 2
        summary["performance_metrics"]["max_volume_spike"] = max(
            summary["performance_metrics"]["max_volume_spike"], rel_volume
        )

def archive_daily_logs():
    """Archive completed daily logs and cleanup"""
    try:
        today = datetime.datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Archive yesterday's logs if they exist
        yesterday_file = os.path.join(LOGS_DIR, f"scanner_hits_{yesterday}.json")
        if os.path.exists(yesterday_file):
            # Create archive directory
            archive_dir = os.path.join(LOGS_DIR, "archive")
            if not os.path.exists(archive_dir):
                os.makedirs(archive_dir)
            
            # Move to archive
            archive_file = os.path.join(archive_dir, f"scanner_hits_{yesterday}.json")
            os.rename(yesterday_file, archive_file)
            
        # Clean up old archives (keep last 30 days)
        cutoff_date = datetime.datetime.now() - datetime.timedelta(days=30)
        archive_dir = os.path.join(LOGS_DIR, "archive")
        if os.path.exists(archive_dir):
            for filename in os.listdir(archive_dir):
                if filename.startswith("scanner_hits_") and filename.endswith(".json"):
                    try:
                        file_date_str = filename.replace("scanner_hits_", "").replace(".json", "")
                        file_date = datetime.datetime.strptime(file_date_str, "%Y-%m-%d")
                        if file_date < cutoff_date:
                            os.remove(os.path.join(archive_dir, filename))
                    except ValueError:
                        pass  # Skip malformed filenames
        
        # Create permanent backup before cleanup
        create_permanent_backup(yesterday)
        
        # Optional cloud backup
        if CLOUD_BACKUP_URL:
            upload_to_cloud_backup(yesterday)
        
        return True
    except Exception as e:
        print(f"Error archiving logs: {e}")
        return False

def save_hit_to_database(hit_entry):
    """Save hit to permanent SQLite database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        stock_data = hit_entry['stock_data']
        trigger_data = hit_entry['trigger_analysis']
        
        cursor.execute('''
            INSERT INTO scanner_hits (
                hit_id, date, timestamp, time_readable, market_session,
                ticker, price, price_change_pct, price_category, volume,
                relative_volume, volume_category, momentum_score,
                primary_trigger, trigger_description, breakout_detected,
                news_detected, signal_strength, risk_level, scanner_criteria
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            hit_entry['hit_id'],
            hit_entry['timestamp'][:10],  # Date only
            hit_entry['timestamp'],
            hit_entry['time_readable'],
            hit_entry['market_session'],
            stock_data['ticker'],
            stock_data['price'],
            stock_data['price_change_pct'],
            stock_data['price_category'],
            stock_data['volume'],
            stock_data['relative_volume'],
            stock_data['volume_category'],
            stock_data['momentum_score'],
            trigger_data['primary_trigger'],
            trigger_data['trigger_description'],
            1 if trigger_data['breakout_detected'] else 0,
            1 if trigger_data['news_detected'] else 0,
            trigger_data['signal_strength'],
            trigger_data['risk_level'],
            hit_entry['context']['scanner_criteria']
        ))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Error saving to database: {e}")

def create_permanent_backup(date):
    """Create permanent backup of archived data"""
    try:
        # Check if archived file exists
        archive_file = os.path.join(LOGS_DIR, "archive", f"scanner_hits_{date}.json")
        if not os.path.exists(archive_file):
            return
        
        # Copy to permanent backup directory with timestamp
        backup_filename = f"scanner_hits_{date}_backup_{int(time.time())}.json"
        backup_path = os.path.join(BACKUP_DIR, backup_filename)
        
        import shutil
        shutil.copy2(archive_file, backup_path)
        
        # Also save daily summary to database
        with open(archive_file, 'r') as f:
            data = json.load(f)
            if 'daily_summary' in data:
                save_daily_summary_to_db(date, data['daily_summary'])
        
        print(f"Created permanent backup: {backup_filename}")
        
    except Exception as e:
        print(f"Error creating permanent backup: {e}")

def save_daily_summary_to_db(date, summary):
    """Save daily summary to database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO daily_summaries (
                date, total_hits, unique_tickers, trigger_types,
                price_ranges, performance_metrics
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            date,
            summary['total_hits'],
            len(summary['unique_tickers']),
            json.dumps(summary['trigger_types']),
            json.dumps(summary['price_ranges']),
            json.dumps(summary['performance_metrics'])
        ))
        
        conn.commit()
        conn.close()
        
    except Exception as e:
        print(f"Error saving daily summary to DB: {e}")

def query_historical_data(start_date=None, end_date=None, ticker=None, limit=1000):
    """Query historical data from database"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        query = "SELECT * FROM scanner_hits WHERE 1=1"
        params = []
        
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker.upper())
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        cursor.execute(query, params)
        rows = cursor.fetchall()
        
        # Convert to list of dictionaries
        columns = [description[0] for description in cursor.description]
        results = [dict(zip(columns, row)) for row in rows]
        
        conn.close()
        return results
        
    except Exception as e:
        print(f"Error querying historical data: {e}")
        return []

def get_daily_summary(date=None):
    """Get summary of scanner hits for a specific date"""
    try:
        if not date:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        # Check current logs first
        log_file = os.path.join(LOGS_DIR, f"scanner_hits_{date}.json")
        if not os.path.exists(log_file):
            # Check archive
            archive_file = os.path.join(LOGS_DIR, "archive", f"scanner_hits_{date}.json")
            if os.path.exists(archive_file):
                log_file = archive_file
            else:
                return {"date": date, "total_hits": 0, "unique_tickers": 0, "scanner_hits": []}
        
        with open(log_file, 'r') as f:
            logs = json.load(f)
        
        # Return the structured data (now ChatGPT friendly)
        if "daily_summary" in logs:
            summary = logs["daily_summary"].copy()
            summary["date"] = date
            summary["unique_tickers"] = len(summary["unique_tickers"])
            summary["scanner_hits"] = logs.get("scanner_hits", [])
            summary["log_metadata"] = logs.get("log_metadata", {})
            return summary
        else:
            # Legacy format fallback
            hits = logs.get("hits", [])
            return {
                "date": date,
                "total_hits": len(hits),
                "unique_tickers": len(set(hit["ticker"] for hit in hits)),
                "scanner_hits": hits
            }
    except Exception as e:
        print(f"Error getting daily summary: {e}")
        return {"date": date, "error": str(e)}

@app.route("/log-hit", methods=["POST"])
def log_hit():
    """Log a scanner hit"""
    try:
        data = request.get_json()
        if not data or not data.get("ticker"):
            return jsonify({"error": "ticker required"}), 400
        
        ticker = data.get("ticker").upper()
        trigger_data = {
            "trigger_type": data.get("trigger_type", "unknown"),
            "price": data.get("price"),
            "change_pct": data.get("change_pct"),
            "volume": data.get("volume"),
            "rel_volume": data.get("rel_volume"),
            "breakout_detected": data.get("breakout_detected", False),
            "news_detected": data.get("news_detected", False),
            "momentum_score": data.get("momentum_score")
        }
        
        success = log_scanner_hit(ticker, trigger_data)
        
        if success:
            return jsonify({"status": "logged", "ticker": ticker}), 200
        else:
            return jsonify({"error": "failed to log"}), 500
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/daily-summary")
def daily_summary():
    """Get daily summary of scanner hits"""
    date = request.args.get("date")  # Optional: YYYY-MM-DD format
    summary = get_daily_summary(date)
    return jsonify(summary), 200

@app.route("/archive-logs", methods=["POST"])
def archive_logs():
    """Archive daily logs (typically called automatically)"""
    try:
        success = archive_daily_logs()
        if success:
            return jsonify({"status": "archived"}), 200
        else:
            return jsonify({"error": "archive failed"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/recent-news")
def recent_news_ticker():
    """Check if a specific ticker has recent news mentions"""
    ticker = (request.args.get("ticker") or "").upper()
    if not ticker:
        return jsonify({"error": "ticker parameter required"}), 400
    
    try:
        # Get recent market news
        url = "https://finnhub.io/api/v1/news"
        params = {
            'category': 'general',
            'token': FINNHUB_API_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        news_data = response.json()
        
        # Filter to very recent news (last 2 hours) and check for ticker mentions
        two_hours_ago = datetime.datetime.now() - datetime.timedelta(hours=2)
        ticker_mentions = 0
        recent_articles = []
        
        for item in news_data[:50]:  # Check recent articles
            news_time = datetime.datetime.fromtimestamp(item.get("datetime", 0))
            if news_time > two_hours_ago:
                headline = item.get("headline", "").upper()
                summary = item.get("summary", "").upper()
                
                # Check if ticker is mentioned
                if ticker in headline or ticker in summary or f"${ticker}" in headline or f"${ticker}" in summary:
                    ticker_mentions += 1
                    recent_articles.append({
                        "headline": item.get("headline", ""),
                        "summary": item.get("summary", "")[:100] + "..." if len(item.get("summary", "")) > 100 else item.get("summary", ""),
                        "source": item.get("source", ""),
                        "datetime": news_time.isoformat()
                    })
        
        return jsonify({
            "ticker": ticker,
            "has_recent_news": ticker_mentions > 0,
            "news_count": ticker_mentions,
            "articles": recent_articles[:3]  # Limit to 3 most recent
        }), 200
        
    except Exception as e:
        return jsonify({"ticker": ticker, "error": str(e)}), 500

@app.route("/export-analysis")
def export_analysis():
    """Export scanner data in ChatGPT-friendly analysis format"""
    try:
        date = request.args.get("date")  # Optional: YYYY-MM-DD format
        days = int(request.args.get("days", 1))  # Number of days to analyze
        
        analysis_data = {
            "analysis_metadata": {
                "generated_at": datetime.datetime.now().isoformat(),
                "analysis_type": "momentum_scanner_performance",
                "date_range": {
                    "start_date": date if date else "today",
                    "days_analyzed": days
                },
                "chatgpt_instructions": {
                    "purpose": "Analyze stock scanner performance and trading opportunities",
                    "focus_areas": [
                        "Most frequent trigger types",
                        "Best performing price ranges",
                        "Volume spike patterns",
                        "Risk assessment accuracy",
                        "Market session effectiveness"
                    ],
                    "data_structure": "Each hit contains: stock_data, trigger_analysis, context, and performance metrics"
                }
            },
            "summary_statistics": {},
            "detailed_hits": [],
            "pattern_analysis": {
                "top_tickers": {},
                "trigger_effectiveness": {},
                "timing_patterns": {},
                "risk_distribution": {}
            }
        }
        
        # Collect data for the specified period
        if not date:
            date = datetime.datetime.now().strftime("%Y-%m-%d")
        
        start_date = datetime.datetime.strptime(date, "%Y-%m-%d")
        all_hits = []
        
        for i in range(days):
            current_date = (start_date - datetime.timedelta(days=i)).strftime("%Y-%m-%d")
            day_summary = get_daily_summary(current_date)
            
            if "scanner_hits" in day_summary:
                all_hits.extend(day_summary["scanner_hits"])
        
        # Calculate comprehensive statistics
        if all_hits:
            analysis_data["summary_statistics"] = calculate_comprehensive_stats(all_hits)
            analysis_data["detailed_hits"] = all_hits[-50:]  # Last 50 hits for detailed analysis
            analysis_data["pattern_analysis"] = analyze_patterns(all_hits)
        
        return jsonify(analysis_data), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def calculate_comprehensive_stats(hits):
    """Calculate comprehensive statistics for ChatGPT analysis"""
    total_hits = len(hits)
    if total_hits == 0:
        return {}
    
    # Basic metrics
    tickers = [hit["stock_data"]["ticker"] for hit in hits]
    unique_tickers = len(set(tickers))
    
    # Trigger type distribution
    trigger_types = [hit["trigger_analysis"]["primary_trigger"] for hit in hits]
    trigger_distribution = {}
    for tt in trigger_types:
        trigger_distribution[tt] = trigger_distribution.get(tt, 0) + 1
    
    # Price and performance metrics
    prices = [hit["stock_data"]["price"] for hit in hits if hit["stock_data"]["price"]]
    changes = [hit["stock_data"]["price_change_pct"] for hit in hits if hit["stock_data"]["price_change_pct"]]
    volumes = [hit["stock_data"]["relative_volume"] for hit in hits if hit["stock_data"]["relative_volume"]]
    
    return {
        "total_hits": total_hits,
        "unique_tickers": unique_tickers,
        "hit_frequency": round(total_hits / unique_tickers, 2) if unique_tickers > 0 else 0,
        "trigger_distribution": trigger_distribution,
        "price_statistics": {
            "avg_price": round(sum(prices) / len(prices), 2) if prices else 0,
            "min_price": min(prices) if prices else 0,
            "max_price": max(prices) if prices else 0
        },
        "performance_statistics": {
            "avg_change_pct": round(sum(changes) / len(changes), 2) if changes else 0,
            "max_change_pct": max(changes) if changes else 0,
            "min_change_pct": min(changes) if changes else 0
        },
        "volume_statistics": {
            "avg_volume_spike": round(sum(volumes) / len(volumes), 2) if volumes else 0,
            "max_volume_spike": max(volumes) if volumes else 0,
            "extreme_spikes_count": len([v for v in volumes if v >= 50])
        }
    }

def analyze_patterns(hits):
    """Analyze patterns for ChatGPT insights"""
    from collections import Counter
    
    # Top tickers by frequency
    tickers = [hit["stock_data"]["ticker"] for hit in hits]
    top_tickers = dict(Counter(tickers).most_common(10))
    
    # Market session effectiveness
    sessions = [hit["market_session"] for hit in hits]
    session_distribution = dict(Counter(sessions))
    
    # Risk level patterns
    risk_levels = [hit["trigger_analysis"]["risk_level"] for hit in hits]
    risk_distribution = dict(Counter(risk_levels))
    
    # Signal strength patterns
    signal_strengths = [hit["trigger_analysis"]["signal_strength"] for hit in hits]
    avg_signal_strength = sum(signal_strengths) / len(signal_strengths) if signal_strengths else 0
    
    return {
        "top_tickers": top_tickers,
        "session_effectiveness": session_distribution,
        "risk_distribution": risk_distribution,
        "signal_strength_avg": round(avg_signal_strength, 2),
        "high_confidence_signals": len([s for s in signal_strengths if s >= 8])
    }

@app.route("/historical-data")
def historical_data():
    """Get historical scanner data from permanent database"""
    try:
        start_date = request.args.get("start_date")  # YYYY-MM-DD
        end_date = request.args.get("end_date")      # YYYY-MM-DD
        ticker = request.args.get("ticker")
        limit = int(request.args.get("limit", 1000))
        
        results = query_historical_data(start_date, end_date, ticker, limit)
        
        return jsonify({
            "total_records": len(results),
            "query_params": {
                "start_date": start_date,
                "end_date": end_date,
                "ticker": ticker,
                "limit": limit
            },
            "data": results
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/database-stats")
def database_stats():
    """Get database statistics and storage info"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # Count total hits
        cursor.execute("SELECT COUNT(*) FROM scanner_hits")
        total_hits = cursor.fetchone()[0]
        
        # Count unique tickers
        cursor.execute("SELECT COUNT(DISTINCT ticker) FROM scanner_hits")
        unique_tickers = cursor.fetchone()[0]
        
        # Date range
        cursor.execute("SELECT MIN(date), MAX(date) FROM scanner_hits")
        date_range = cursor.fetchone()
        
        # Get database file size
        db_size_mb = os.path.getsize(DB_PATH) / (1024 * 1024) if os.path.exists(DB_PATH) else 0
        
        # Count backup files
        backup_count = len([f for f in os.listdir(BACKUP_DIR) if f.endswith('.json')]) if os.path.exists(BACKUP_DIR) else 0
        
        conn.close()
        
        return jsonify({
            "permanent_storage": {
                "database_size_mb": round(db_size_mb, 2),
                "total_hits_stored": total_hits,
                "unique_tickers": unique_tickers,
                "date_range": {
                    "first_hit": date_range[0],
                    "latest_hit": date_range[1]
                },
                "backup_files": backup_count
            },
            "storage_status": "All data permanently preserved",
            "retention_policy": "Infinite - data never deleted from database"
        }), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)

import requests
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import os
from dotenv import load_dotenv

class StockScreener:
    def __init__(self, polygon_api_key: str, finnhub_api_key: str):
        self.polygon_api_key = polygon_api_key
        self.finnhub_api_key = finnhub_api_key
        self.polygon_base_url = "https://api.polygon.io"
        self.finnhub_base_url = "https://finnhub.io/api/v1"
        
    def get_polygon_data(self, endpoint: str, params: Dict = None) -> Dict:
        """Fetch data from Polygon.io API"""
        if params is None:
            params = {}
        params['apikey'] = self.polygon_api_key
        
        url = f"{self.polygon_base_url}{endpoint}"
        response = requests.get(url, params=params)
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Polygon API error: {response.status_code} - {response.text}")
            return {}
    
    def get_finnhub_data(self, endpoint: str, params: Dict = None) -> Dict:
        """Fetch data from Finnhub API"""
        if params is None:
            params = {}
        params['token'] = self.finnhub_api_key
        
        url = f"{self.finnhub_base_url}{endpoint}"
        response = requests.get(url, params=params)
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Finnhub API error: {response.status_code} - {response.text}")
            return {}
    
    def get_stock_tickers(self, market: str = "stocks", limit: int = 100) -> List[str]:
        """Get list of stock tickers from Polygon"""
        endpoint = "/v3/reference/tickers"
        params = {
            'market': market,
            'active': 'true',
            'limit': limit
        }
        
        data = self.get_polygon_data(endpoint, params)
        tickers = []
        
        if 'results' in data:
            for result in data['results']:
                if result.get('market') == 'stocks':
                    tickers.append(result['ticker'])
        
        return tickers
    
    def get_stock_financials(self, symbol: str) -> Dict:
        """Get basic financial data from Finnhub"""
        endpoint = "/stock/metric"
        params = {'symbol': symbol, 'metric': 'all'}
        
        return self.get_finnhub_data(endpoint, params)
    
    def get_shares_outstanding(self, symbol: str) -> float:
        """Get shares outstanding from Polygon to calculate float"""
        endpoint = f"/v3/reference/tickers/{symbol}"
        
        data = self.get_polygon_data(endpoint)
        if 'results' in data:
            # Try to get shares outstanding
            return data['results'].get('share_class_shares_outstanding', 0)
        return 0
    
    def get_company_news(self, symbol: str) -> List[Dict]:
        """Get recent company news from Finnhub"""
        endpoint = "/company-news"
        from_date = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        to_date = datetime.now().strftime('%Y-%m-%d')
        
        params = {
            'symbol': symbol,
            'from': from_date,
            'to': to_date
        }
        
        data = self.get_finnhub_data(endpoint, params)
        return data if isinstance(data, list) else []
    
    def get_average_volume(self, symbol: str, days: int = 30) -> float:
        """Get average volume over specified days from Polygon"""
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
        
        endpoint = f"/v2/aggs/ticker/{symbol}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        
        data = self.get_polygon_data(endpoint)
        if 'results' in data and data['results']:
            volumes = [result['v'] for result in data['results'] if 'v' in result]
            return sum(volumes) / len(volumes) if volumes else 0
        return 0
    
    def get_stock_quote(self, symbol: str) -> Dict:
        """Get current quote from Finnhub"""
        endpoint = "/quote"
        params = {'symbol': symbol}
        
        return self.get_finnhub_data(endpoint, params)
    
    def get_previous_close(self, symbol: str) -> Dict:
        """Get previous close data from Polygon"""
        endpoint = f"/v2/aggs/ticker/{symbol}/prev"
        
        return self.get_polygon_data(endpoint)
    
    def screen_stocks(self, criteria: Dict) -> List[Dict]:
        """Screen stocks based on momentum trading criteria"""
        print("Fetching stock tickers for momentum screening...")
        tickers = self.get_stock_tickers(limit=100)  # Increased limit for better screening
        
        screened_stocks = []
        
        for i, ticker in enumerate(tickers):
            print(f"Processing {ticker} ({i+1}/{len(tickers)})")
            
            try:
                # Get quote data
                quote = self.get_stock_quote(ticker)
                if not quote or 'c' not in quote:
                    continue
                
                current_price = quote['c']
                if current_price <= 0:
                    continue
                
                # Get previous close for volume and price change
                prev_data = self.get_previous_close(ticker)
                volume = 0
                price_change_pct = 0
                
                if 'results' in prev_data and prev_data['results']:
                    result = prev_data['results'][0]
                    volume = result.get('v', 0)
                    prev_close = result.get('c', current_price)
                    if prev_close > 0:
                        price_change_pct = ((current_price - prev_close) / prev_close) * 100
                
                # Quick price and volume filter first
                if not self._meets_basic_criteria(current_price, volume, price_change_pct, criteria):
                    continue
                
                # Get additional data for momentum stocks
                print(f"  Getting detailed data for {ticker}...")
                
                # Get shares outstanding for float calculation
                shares_outstanding = self.get_shares_outstanding(ticker)
                float_millions = shares_outstanding / 1_000_000 if shares_outstanding > 0 else 999  # Default high if unknown
                
                # Get average volume for relative volume calculation
                avg_volume = self.get_average_volume(ticker)
                relative_volume = volume / avg_volume if avg_volume > 0 else 0
                
                # Get recent news for catalyst detection
                news = self.get_company_news(ticker)
                has_catalyst = self._detect_catalyst(news)
                
                # Apply full screening criteria
                if self._meets_momentum_criteria(current_price, volume, price_change_pct, 
                                               float_millions, relative_volume, has_catalyst, criteria):
                    stock_data = {
                        'symbol': ticker,
                        'price': current_price,
                        'volume': volume,
                        'change_pct': price_change_pct,
                        'high': quote.get('h', 0),
                        'low': quote.get('l', 0),
                        'open': quote.get('o', 0),
                        'float_millions': float_millions,
                        'relative_volume': relative_volume,
                        'avg_volume': avg_volume,
                        'has_catalyst': has_catalyst,
                        'news_count': len(news)
                    }
                    screened_stocks.append(stock_data)
                    print(f"  ✓ {ticker} meets criteria - {price_change_pct:.1f}% gain, {relative_volume:.1f}x volume")
                
                # Rate limiting
                time.sleep(0.2)
                
            except Exception as e:
                print(f"Error processing {ticker}: {str(e)}")
                continue
        
        return screened_stocks
    
    def _meets_basic_criteria(self, price: float, volume: int, change_pct: float, criteria: Dict) -> bool:
        """Quick basic criteria check to filter out obviously unsuitable stocks"""
        # Price range
        if price < criteria.get('min_price', 2.0) or price > criteria.get('max_price', 20.0):
            return False
        
        # Minimum volume
        if volume < criteria.get('min_volume', 100000):
            return False
        
        # Must be a significant gainer
        if change_pct < criteria.get('min_change_pct', 10.0):
            return False
        
        return True
    
    def _meets_momentum_criteria(self, price: float, volume: int, change_pct: float, 
                               float_millions: float, relative_volume: float, has_catalyst: bool, criteria: Dict) -> bool:
        """Check if stock meets full momentum trading criteria"""
        # High percentage gainers (10%+ intraday)
        if change_pct < criteria.get('min_change_pct', 10.0):
            return False
        
        # Affordable price range ($2–$20)
        if price < criteria.get('min_price', 2.0) or price > criteria.get('max_price', 20.0):
            return False
        
        # Low float (<20M shares)
        if float_millions > criteria.get('max_float_millions', 20.0):
            return False
        
        # High relative volume (5x+ average)
        if relative_volume < criteria.get('min_relative_volume', 5.0):
            return False
        
        # Strong catalyst (optional but preferred)
        if criteria.get('require_catalyst', False) and not has_catalyst:
            return False
        
        return True
    
    def _detect_catalyst(self, news: List[Dict]) -> bool:
        """Detect if there's a strong catalyst in recent news"""
        if not news:
            return False
        
        catalyst_keywords = [
            'earnings', 'fda', 'approval', 'acquisition', 'merger', 'partnership',
            'contract', 'breakthrough', 'clinical', 'trial', 'results', 'guidance',
            'upgrade', 'downgrade', 'analyst', 'buyout', 'dividend', 'split',
            'patent', 'launch', 'expansion', 'revenue', 'beat', 'miss'
        ]
        
        for article in news:
            headline = article.get('headline', '').lower()
            summary = article.get('summary', '').lower()
            
            # Check for catalyst keywords in headline or summary
            for keyword in catalyst_keywords:
                if keyword in headline or keyword in summary:
                    return True
        
        return False
    
    def display_results(self, stocks: List[Dict]):
        """Display momentum screening results in a formatted table"""
        if not stocks:
            print("No momentum stocks found matching criteria.")
            return
        
        print(f"\nFound {len(stocks)} momentum stocks matching your strategy:")
        print("-" * 120)
        print(f"{'Symbol':<8} {'Price':<8} {'Gain %':<8} {'Volume':<12} {'Rel Vol':<8} {'Float(M)':<10} {'Catalyst':<9} {'News':<5}")
        print("-" * 120)
        
        for stock in sorted(stocks, key=lambda x: x['change_pct'], reverse=True):
            catalyst_indicator = "YES" if stock.get('has_catalyst', False) else "NO"
            print(f"{stock['symbol']:<8} "
                  f"${stock['price']:<7.2f} "
                  f"{stock['change_pct']:<7.1f}% "
                  f"{stock['volume']:<12,} "
                  f"{stock.get('relative_volume', 0):<7.1f}x "
                  f"{stock.get('float_millions', 0):<9.1f} "
                  f"{catalyst_indicator:<9} "
                  f"{stock.get('news_count', 0):<5}")
        
        print(f"\nStrategy Summary:")
        print(f"   - High gainers: All stocks up 10%+ today")
        print(f"   - Low float: All under 20M shares outstanding")  
        print(f"   - High volume: All trading 5x+ normal volume")
        print(f"   - Price range: $2-$20 (affordable entry)")
        if any(stock.get('has_catalyst') for stock in stocks):
            print(f"   - Catalysts detected: {sum(1 for stock in stocks if stock.get('has_catalyst'))} stocks have news catalysts")
    
    def generate_html_report(self, stocks: List[Dict], criteria: Dict, filename: str = "stock_screener_results.html"):
        """Generate an HTML report of screening results"""
        html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Stock Screener Results</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 10px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            overflow: hidden;
        }
        .header {
            background: linear-gradient(135deg, #2c3e50 0%, #34495e 100%);
            color: white;
            padding: 30px;
            text-align: center;
        }
        .header h1 {
            margin: 0;
            font-size: 2.5em;
            font-weight: 300;
        }
        .criteria {
            background: #ecf0f1;
            padding: 20px 30px;
            border-bottom: 1px solid #bdc3c7;
        }
        .criteria h3 {
            margin: 0 0 10px 0;
            color: #2c3e50;
        }
        .criteria-item {
            display: inline-block;
            margin: 5px 15px 5px 0;
            padding: 8px 16px;
            background: #3498db;
            color: white;
            border-radius: 20px;
            font-size: 0.9em;
        }
        .summary {
            padding: 20px 30px;
            text-align: center;
            font-size: 1.2em;
            color: #2c3e50;
        }
        .table-container {
            padding: 0 30px 30px 30px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        th {
            background: linear-gradient(135deg, #3498db 0%, #2980b9 100%);
            color: white;
            padding: 15px;
            text-align: left;
            font-weight: 500;
            border-bottom: 3px solid #2980b9;
        }
        td {
            padding: 12px 15px;
            border-bottom: 1px solid #ecf0f1;
        }
        tr:nth-child(even) {
            background: #f8f9fa;
        }
        tr:hover {
            background: #e8f4f8;
            transform: scale(1.01);
            transition: all 0.2s ease;
        }
        .positive {
            color: #27ae60;
            font-weight: bold;
        }
        .negative {
            color: #e74c3c;
            font-weight: bold;
        }
        .symbol {
            font-weight: bold;
            font-size: 1.1em;
            color: #2c3e50;
        }
        .price {
            font-weight: bold;
            color: #8e44ad;
        }
        .volume {
            color: #7f8c8d;
        }
        .timestamp {
            text-align: center;
            padding: 20px;
            color: #7f8c8d;
            font-size: 0.9em;
            border-top: 1px solid #ecf0f1;
        }
        .no-results {
            text-align: center;
            padding: 50px;
            color: #7f8c8d;
            font-size: 1.2em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🚀 Momentum Stock Screener</h1>
            <p>High % Gainers • Low Float • High Volume • Strong Catalysts</p>
        </div>
        
        <div class="criteria">
            <h3>Your Trading Strategy:</h3>
            <span class="criteria-item">High Gainers: {min_change_pct}%+ intraday</span>
            <span class="criteria-item">Price Range: ${min_price} - ${max_price}</span>
            <span class="criteria-item">Low Float: <{max_float_millions}M shares</span>
            <span class="criteria-item">High Volume: {min_relative_volume}x+ average</span>
        </div>
        
        <div class="summary">
            <strong>{stock_count}</strong> stocks found matching criteria
        </div>
        
        <div class="table-container">
            {table_content}
        </div>
        
        <div class="timestamp">
            Generated on {timestamp}
        </div>
    </div>
</body>
</html>
        """
        
        if not stocks:
            table_content = '<div class="no-results">No stocks found matching the specified criteria.</div>'
        else:
            table_rows = []
            for stock in sorted(stocks, key=lambda x: x['change_pct'], reverse=True):
                change_class = "positive" if stock['change_pct'] >= 0 else "negative"
                change_symbol = "+" if stock['change_pct'] >= 0 else ""
                
                catalyst_indicator = "✅" if stock.get('has_catalyst', False) else "❌"
                row = f"""
                <tr>
                    <td class="symbol">{stock['symbol']}</td>
                    <td class="price">${stock['price']:.2f}</td>
                    <td class="{change_class}">{change_symbol}{stock['change_pct']:.1f}%</td>
                    <td class="volume">{stock['volume']:,}</td>
                    <td class="volume">{stock.get('relative_volume', 0):.1f}x</td>
                    <td class="volume">{stock.get('float_millions', 0):.1f}M</td>
                    <td style="text-align: center;">{catalyst_indicator}</td>
                    <td style="text-align: center;">{stock.get('news_count', 0)}</td>
                </tr>
                """
                table_rows.append(row)
            
            table_content = f"""
            <table>
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Price</th>
                        <th>Gain %</th>
                        <th>Volume</th>
                        <th>Rel Vol</th>
                        <th>Float (M)</th>
                        <th>Catalyst</th>
                        <th>News</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(table_rows)}
                </tbody>
            </table>
            """
        
        html_content = html_template.replace('{min_price}', str(criteria.get('min_price', 2.0)))
        html_content = html_content.replace('{max_price}', str(criteria.get('max_price', 20.0)))
        html_content = html_content.replace('{min_change_pct}', str(criteria.get('min_change_pct', 10.0)))
        html_content = html_content.replace('{max_float_millions}', str(criteria.get('max_float_millions', 20.0)))
        html_content = html_content.replace('{min_relative_volume}', str(criteria.get('min_relative_volume', 5.0)))
        html_content = html_content.replace('{stock_count}', str(len(stocks)))
        html_content = html_content.replace('{table_content}', table_content)
        html_content = html_content.replace('{timestamp}', datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        print(f"\nHTML report generated: {filename}")
        return filename
    

def main():
    # Load environment variables from .env file
    load_dotenv()
    
    # You need to set your API keys as environment variables or replace with your actual keys
    polygon_key = os.getenv('POLYGON_API_KEY', 'YOUR_POLYGON_API_KEY')
    finnhub_key = os.getenv('FINNHUB_API_KEY', 'YOUR_FINNHUB_API_KEY')
    
    if polygon_key == 'YOUR_POLYGON_API_KEY' or finnhub_key == 'YOUR_FINNHUB_API_KEY':
        print("Please set your API keys:")
        print("export POLYGON_API_KEY='your_key_here'")
        print("export FINNHUB_API_KEY='your_key_here'")
        return
    
    screener = StockScreener(polygon_key, finnhub_key)
    
    # Define momentum trading criteria based on your strategy
    criteria = {
        'min_price': 2.0,                    # Affordable price range
        'max_price': 20.0,                   # Maximum affordable price
        'min_volume': 500000,                # Higher minimum volume for momentum
        'min_change_pct': 10.0,             # High % gainers (10%+ intraday)
        'max_float_millions': 20.0,          # Low float (<20M shares)
        'min_relative_volume': 5.0,          # High relative volume (5x+ average)
        'require_catalyst': False            # Catalyst preferred but not required
    }
    
    print("Starting momentum stock screening...")
    print(f"Strategy: High gainers {criteria['min_change_pct']}%+, "
          f"Price ${criteria['min_price']}-${criteria['max_price']}, "
          f"Float <{criteria['max_float_millions']}M, "
          f"Volume {criteria['min_relative_volume']}x+ average")
    
    results = screener.screen_stocks(criteria)
    screener.display_results(results)
    screener.generate_html_report(results, criteria)

if __name__ == "__main__":
    main()
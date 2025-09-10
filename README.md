# 🚀 Momentum Stock Screener

A web-based stock screener designed for momentum trading strategies. Finds high-percentage gainers with low float, high volume, and strong catalysts.

## 🎯 Trading Strategy

This screener identifies stocks that meet specific momentum trading criteria:

- **High % Gainers**: 10%+ intraday price moves
- **Affordable Range**: $2-$20 price range for manageable position sizes
- **Low Float**: <20M shares outstanding (easier to move)
- **High Volume**: 5x+ average relative volume (unusual activity)
- **Strong Catalysts**: Earnings, FDA approvals, partnerships, etc.

## 🚀 Live Demo

**[View Live Demo](https://your-username.github.io/momentum-stock-screener/)**

*Replace with your actual GitHub Pages URL after deployment*

## 📱 Features

- **Real-time Screening**: Configure criteria and scan markets instantly
- **Interactive Interface**: Adjust parameters on the fly
- **Responsive Design**: Works on desktop, tablet, and mobile
- **API Integration**: Uses Polygon.io and Finnhub APIs for live data
- **Catalyst Detection**: Identifies stocks with recent news catalysts
- **Export Results**: View detailed HTML reports

## 🛠️ Setup for Hosting

### Option 1: GitHub Pages (Recommended)

1. **Create a GitHub repository**:
   ```bash
   git init
   git add .
   git commit -m "Initial commit - Momentum Stock Screener"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/momentum-stock-screener.git
   git push -u origin main
   ```

2. **Enable GitHub Pages**:
   - Go to repository Settings → Pages
   - Source: Deploy from a branch
   - Branch: main / (root)
   - Save

3. **Your site will be live at**:
   `https://YOUR_USERNAME.github.io/momentum-stock-screener/`

### Option 2: Netlify

1. Visit [netlify.com](https://netlify.com)
2. Drag and drop your folder to deploy instantly
3. Get a free subdomain or connect your custom domain

### Option 3: Vercel

1. Install Vercel CLI: `npm i -g vercel`
2. Run `vercel` in your project folder
3. Follow the prompts for instant deployment

## 🔑 API Keys

You'll need free API keys from:

- **[Polygon.io](https://polygon.io)**: Stock data and historical information
- **[Finnhub](https://finnhub.io)**: Real-time quotes and company news

Both offer generous free tiers perfect for personal use.

## ⚙️ Configuration

The screener allows you to adjust:

- **Price Range**: Min/max stock price
- **Gain Threshold**: Minimum percentage gain required
- **Float Limit**: Maximum shares outstanding
- **Volume Multiple**: Minimum relative volume ratio
- **Stock Limit**: Number of stocks to screen

## 📊 Output

Results include:
- Stock symbol and current price
- Percentage gain for the day
- Current volume and relative volume
- Float size in millions
- Catalyst indicators
- Recent news count

## 🚧 Important Notes

**CORS Limitations**: Due to browser security, the web version has limitations:
- API calls must be made from a server or CORS-enabled proxy
- Current version includes demo data for demonstration
- For live data, consider deploying with a backend service

**Rate Limits**: 
- Free API tiers have rate limits
- Implement delays between requests
- Consider upgrading for high-frequency use

## 📱 Mobile Friendly

The interface is fully responsive and works great on:
- Desktop computers
- Tablets
- Mobile phones

## 🤝 Sharing with AI

Perfect for sharing with ChatGPT or other AI assistants:
- Clean, semantic HTML structure
- Well-documented code
- Clear parameter descriptions
- Comprehensive README

## 📄 License

This project is open source and available under the MIT License.

## 🙏 Acknowledgments

- Data provided by Polygon.io and Finnhub APIs
- Built for momentum traders and day traders
- Inspired by successful momentum trading strategies

---

**Disclaimer**: This tool is for educational and informational purposes only. Always do your own research before making investment decisions. Past performance does not guarantee future results.
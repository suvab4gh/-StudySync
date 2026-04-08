# Deploy StudySync Frontend to Vercel

## Prerequisites
- Node.js 18+ installed
- Vercel account (free tier works)
- Vercel CLI installed: `npm install -g vercel`

## Quick Deploy Steps

### 1. Login to Vercel
```bash
vercel login
```

### 2. Navigate to frontend directory
```bash
cd frontend
```

### 3. Deploy
```bash
vercel
```

Follow the prompts:
- Set up and deploy? **Y**
- Which scope? (select your account)
- Link to existing project? **N** (for first time)
- Project name? **studysync-frontend** (or your choice)
- Directory? **./** (current directory)
- Override settings? **N**

### 4. Configure Environment Variables (Optional)
If you have a backend API deployed, set the API URL:
```bash
vercel env add VITE_API_URL production
# Enter your backend URL, e.g., https://your-api.onrender.com
```

### 5. Redeploy with env vars
```bash
vercel --prod
```

## One-Click Deploy Alternative

Push your code to GitHub, then:
1. Go to [vercel.com/new](https://vercel.com/new)
2. Import your repository
3. Set root directory to `frontend`
4. Add build command: `npm run build`
5. Add output directory: `dist`
6. Deploy!

## Post-Deploy

Your app will be live at `https://your-project.vercel.app`

### Notes
- The upload feature requires a backend API to be deployed separately
- Without a backend, the UI will work but API calls will fail
- To connect to your backend, set the `VITE_API_URL` environment variable in Vercel dashboard

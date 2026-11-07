# Frontend Reverse Proxy Rule

When implementing a frontend-backend separated web application, always configure a reverse proxy in the frontend to forward API requests to the backend server.

## Background

The current online preview environment can only expose a single port. Therefore, when developing applications with separate frontend and backend services, the frontend must include a reverse proxy configuration to route API requests to the backend.

## Guidelines

When implementing a frontend for a web application with a separate backend:

1. **Configure reverse proxy**: Set up a proxy in the frontend development server to forward API requests to the backend
2. **Use consistent API prefix**: Typically use `/api` as the prefix for backend requests
3. **Ensure both services start**: The preview should start both frontend and backend services

## Implementation by Framework

### Vite (Vue, React, etc.)

In `vite.config.ts` or `vite.config.js`:

```typescript
export default defineConfig({
  server: {
    proxy: {
      '/api': {
        target: 'http://localhost:3001', // Backend server address
        changeOrigin: true,
        // rewrite: (path) => path.replace(/^\/api/, '') // Optional: remove /api prefix
      }
    }
  }
})
```

### Create React App

In `package.json`:

```json
{
  "proxy": "http://localhost:3001"
}
```

Or use `src/setupProxy.js` for more control:

```javascript
const { createProxyMiddleware } = require('http-proxy-middleware');

module.exports = function(app) {
  app.use('/api', createProxyMiddleware({
    target: 'http://localhost:3001',
    changeOrigin: true,
  }));
};
```

### Next.js

In `next.config.js`:

```javascript
module.exports = {
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:3001/api/:path*',
      },
    ];
  },
};
```

### Webpack Dev Server

In `webpack.config.js`:

```javascript
module.exports = {
  devServer: {
    proxy: {
      '/api': {
        target: 'http://localhost:3001',
        changeOrigin: true,
      },
    },
  },
};
```

## Startup Script

Create a startup script that runs both services. Example `start.sh`:

```bash
#!/bin/bash
# Start backend server in background
cd backend && npm run dev &
BACKEND_PID=

# Start frontend server (this will be the exposed port)
cd frontend && npm run dev

# Cleanup on exit
trap "kill " EXIT
```

## Rationale

- Online preview environment only exposes one port to the user
- The frontend development server port is typically the one exposed for preview
- Reverse proxy allows API requests to reach the backend without CORS issues
- This setup mimics production deployment where a single entry point handles all requests

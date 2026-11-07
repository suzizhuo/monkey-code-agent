# DevServer AllowedHosts Configuration Rule

When using a development server in a Node.js project, automatically add `*.monkeycode-ai.online` to the `allowedHosts` configuration.

## Applicable Scenarios

Execute this rule when the following conditions are met:

1. **Project Type Detection**
   - Project contains `package.json`
   - Project uses a development server that supports `allowedHosts` configuration

2. **Detected Frameworks/Tools**

## Configuration by Framework/Tool

### Vite

**Config Files**: `vite.config.js` / `vite.config.ts` / `vite.config.mjs` / `vite.config.mts`

```javascript
export default defineConfig({
  server: {
    allowedHosts: ['.monkeycode-ai.online']
  }
})
```

### Next.js

**Config Files**: `next.config.js` / `next.config.mjs` / `next.config.ts`

Next.js 13.4+:
```javascript
/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    allowedHosts: ['.monkeycode-ai.online']
  }
}

module.exports = nextConfig
```

### Nuxt

**Config Files**: `nuxt.config.ts` / `nuxt.config.js`

```typescript
export default defineNuxtConfig({
  vite: {
    server: {
      allowedHosts: ['.monkeycode-ai.online']
    }
  }
})
```

### Vue CLI

**Config Files**: `vue.config.js`

```javascript
module.exports = {
  devServer: {
    allowedHosts: ['.monkeycode-ai.online']
  }
}
```

### Create React App

**Config Files**: `craco.config.js` (when using CRACO)

```javascript
module.exports = {
  devServer: {
    allowedHosts: ['.monkeycode-ai.online']
  }
}
```

### SvelteKit

**Config Files**: `svelte.config.js` / `vite.config.js`

```javascript
// vite.config.js
export default {
  server: {
    allowedHosts: ['.monkeycode-ai.online']
  }
}
```

### Astro

**Config Files**: `astro.config.mjs` / `astro.config.ts`

```javascript
export default defineConfig({
  server: {
    allowedHosts: ['.monkeycode-ai.online']
  }
})
```

### Umi

**Config Files**: `.umirc.ts` / `config/config.ts`

```typescript
export default {
  devServer: {
    allowedHosts: ['.monkeycode-ai.online']
  }
}
```

### Remix

**Config Files**: `vite.config.ts`

```typescript
export default {
  server: {
    allowedHosts: ['.monkeycode-ai.online']
  }
}
```

### SolidStart

**Config Files**: `vite.config.ts`

```typescript
export default {
  server: {
    allowedHosts: ['.monkeycode-ai.online']
  }
}
```

### Generic webpack-dev-server

**Config Files**: `webpack.config.js` / `webpack.dev.js`

```javascript
module.exports = {
  devServer: {
    allowedHosts: ['.monkeycode-ai.online']
  }
}
```

## Detection Logic

### Step 1: Identify Project Type

Check dependencies in `package.json`:

```bash
# Check for frameworks
grep -E '"(vite|next|nuxt|@sveltejs/kit|astro|umi|@remix-run/dev|solid-start|webpack)"' package.json
```

### Step 2: Read Corresponding Config File

Read the appropriate configuration file based on the detected framework.

### Step 3: Update Configuration

Check if `allowedHosts` or `server` / `devServer` configuration exists, handle in the following priority order:

1. `allowedHosts` already exists → Add `.monkeycode-ai.online`
2. `server` / `devServer` exists but no `allowedHosts` → Add new configuration
3. No `server` / `devServer` → Add complete configuration object

## Notes

1. **Domain Format**: Use `.monkeycode-ai.online` (note the leading dot) as a wildcard
2. **Configuration Location**:
   - Vite-based: in `server.allowedHosts`
   - Webpack-based: in `devServer.allowedHosts`
   - Next.js: in `experimental.allowedHosts`
3. **Syntax Compatibility**: Use correct syntax based on config file type (JS/TS)
4. **Preserve Existing Config**: Do not overwrite other existing configuration items

## Verification

After configuration, verify as follows:

```bash
# Start development server
npm run dev

# Check server logs to confirm allowedHosts is loaded correctly
```

If configured correctly, accessing the development server via `*.monkeycode-ai.online` should work properly.

# Global Package Installation Rule

When installing packages using package managers like npm, yarn, pip, etc., always use global installation mode.

## Required Installation Flags

### Node.js Package Managers
- `npm install -g <package>` - use `-g` flag for global installation
- `yarn global add <package>` - use `global add` for global installation
- `pnpm add -g <package>` - use `-g` flag for global installation

### Python Package Managers
- `pip install --break-system-packages <package>` - use `--break-system-packages` flag
- `pip3 install --break-system-packages <package>` - use `--break-system-packages` flag

## Rationale

This ensures packages are installed in a consistent global location and avoids permission issues with local installations in restricted environments.

import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import fs from 'fs/promises';
import {defineConfig, loadEnv, type Plugin} from 'vite';

function apiPlugin(): Plugin {
  return {
    name: 'icr-api',
    configureServer(server) {
      // POST /api/export — write file to filesystem, create dirs
      server.middlewares.use('/api/export', async (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; res.end(); return; }
        let body = '';
        req.on('data', (chunk: string) => body += chunk);
        req.on('end', async () => {
          try {
            const { directory, filename, content } = JSON.parse(body);
            const dir = path.resolve(directory);
            await fs.mkdir(dir, { recursive: true });
            const filePath = path.join(dir, filename);
            await fs.writeFile(filePath, content, 'utf-8');
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ success: true, path: filePath }));
          } catch (err: any) {
            res.statusCode = 500;
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ success: false, error: err.message }));
          }
        });
      });

      // POST /api/save-config — save config JSON to project root
      server.middlewares.use('/api/save-config', async (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; res.end(); return; }
        let body = '';
        req.on('data', (chunk: string) => body += chunk);
        req.on('end', async () => {
          try {
            const config = JSON.parse(body);
            await fs.writeFile(
              path.resolve('icr-config.json'),
              JSON.stringify(config, null, 2),
              'utf-8'
            );
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ success: true }));
          } catch (err: any) {
            res.statusCode = 500;
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ success: false, error: err.message }));
          }
        });
      });

      // GET /api/load-config — read config from project root
      server.middlewares.use('/api/load-config', async (req, res) => {
        if (req.method !== 'GET') { res.statusCode = 405; res.end(); return; }
        try {
          const data = await fs.readFile(path.resolve('icr-config.json'), 'utf-8');
          res.setHeader('Content-Type', 'application/json');
          res.end(data);
        } catch {
          res.setHeader('Content-Type', 'application/json');
          res.end(JSON.stringify(null));
        }
      });

      // POST /api/list-local — list .json files in a local directory
      server.middlewares.use('/api/list-local', async (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; res.end(); return; }
        let body = '';
        req.on('data', (chunk: string) => body += chunk);
        req.on('end', async () => {
          try {
            const { directory } = JSON.parse(body);
            const dir = path.resolve(directory);
            const entries = await fs.readdir(dir);
            const jsonFiles = entries
              .filter(e => e.endsWith('.json'))
              .map(name => ({
                name: name.replace('.json', ''),
                path: `local:${path.join(dir, name)}`,
                download_url: `local:${path.join(dir, name)}`,
              }));
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify(jsonFiles));
          } catch (err: any) {
            res.statusCode = 500;
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ error: err.message }));
          }
        });
      });

      // POST /api/read-local — read a local JSON file
      server.middlewares.use('/api/read-local', async (req, res) => {
        if (req.method !== 'POST') { res.statusCode = 405; res.end(); return; }
        let body = '';
        req.on('data', (chunk: string) => body += chunk);
        req.on('end', async () => {
          try {
            const { filePath } = JSON.parse(body);
            const data = await fs.readFile(filePath, 'utf-8');
            res.setHeader('Content-Type', 'application/json');
            res.end(data);
          } catch (err: any) {
            res.statusCode = 500;
            res.setHeader('Content-Type', 'application/json');
            res.end(JSON.stringify({ error: err.message }));
          }
        });
      });
    },
  };
}

export default defineConfig(({mode}) => {
  const env = loadEnv(mode, '.', '');
  return {
    plugins: [react(), tailwindcss(), apiPlugin()],
    define: {
      'process.env.GEMINI_API_KEY': JSON.stringify(env.GEMINI_API_KEY),
    },
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      hmr: process.env.DISABLE_HMR !== 'true',
    },
  };
});

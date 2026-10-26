import { defineConfig } from 'vite';
export default defineConfig({server:{port:5173,proxy:{'/api':'http://127.0.0.1:8502','/auth':'http://127.0.0.1:8502'}},build:{outDir:'dist'}});

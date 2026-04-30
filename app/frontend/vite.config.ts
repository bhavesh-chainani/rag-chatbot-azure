import { defineConfig, Plugin } from "vite";
import { resolve } from "path";
import react from "@vitejs/plugin-react";
import fs from "fs";

function msalRedirectPlugin(): Plugin {
    return {
        name: "msal-redirect",
        configureServer(server) {
            server.middlewares.use((req, res, next) => {
                if (req.url === "/redirect" || req.url?.startsWith("/redirect?") || req.url?.startsWith("/redirect#")) {
                    const redirectHtml = fs.readFileSync(resolve(__dirname, "redirect.html"), "utf-8");
                    server.transformIndexHtml("/redirect.html", redirectHtml).then(html => {
                        res.setHeader("Content-Type", "text/html");
                        res.end(html);
                    });
                } else {
                    next();
                }
            });
        }
    };
}

// https://vitejs.dev/config/
export default defineConfig({
    plugins: [msalRedirectPlugin(), react()],
    resolve: {
        preserveSymlinks: true
    },
    build: {
        outDir: "../backend/static",
        emptyOutDir: true,
        sourcemap: true,
        rollupOptions: {
            input: {
                main: resolve(__dirname, "index.html"),
                redirect: resolve(__dirname, "redirect.html")
            },
            output: {
                manualChunks: id => {
                    if (id.includes("@fluentui/react-icons")) {
                        return "fluentui-icons";
                    } else if (id.includes("@fluentui/react")) {
                        return "fluentui-react";
                    } else if (id.includes("node_modules")) {
                        return "vendor";
                    }
                }
            }
        },
        target: "esnext"
    },
    server: {
        proxy: {
            "/content/": "http://localhost:50505",
            "/auth_setup": "http://localhost:50505",
            "/.auth/me": "http://localhost:50505",
            "/chat": "http://localhost:50505",
            "/speech": "http://localhost:50505",
            "/config": "http://localhost:50505",
            "/upload": "http://localhost:50505",
            "/delete_uploaded": "http://localhost:50505",
            "/list_uploaded": "http://localhost:50505",
            "/chat_history": "http://localhost:50505"
        }
    }
});

// Backend base URL. Empty = same origin, used locally where FastAPI also serves this folder.
// On Vercel the frontend talks to the VM backend (Dynu DNS + certbot HTTPS).
window.VIVA_API = ["localhost", "127.0.0.1"].includes(location.hostname) ? "" : "https://islinger19.ddnsfree.com";

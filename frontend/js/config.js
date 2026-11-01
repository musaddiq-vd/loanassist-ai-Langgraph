// On AWS the API is served by CloudFront on the same domain, so a relative URL works.
// For local development (frontend on localhost:5500), call the local uvicorn server.
window.LOANASSIST_API_URL = ["localhost", "127.0.0.1"].includes(window.location.hostname)
  ? "http://localhost:8000/api/v1/chat"
  : "/api/v1/chat";

// Sign-in settings. deploy.ps1 overwrites this file in S3 with the real Cognito
// values from the stack outputs. Left as null, the app runs without sign-in and
// keeps chat history in this browser only (handy for local development).
window.LOANASSIST_AUTH = null;

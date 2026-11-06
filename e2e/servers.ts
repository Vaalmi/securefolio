// Where the end-to-end tests run their own servers, on ports of their own so they
// never touch what `npm run dev` started.

export const BACKEND = "http://127.0.0.1:8100";
export const APP = "http://127.0.0.1:5199";
export const VAULT = "e2e/.vault";

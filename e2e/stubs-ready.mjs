// The backend remembers for 30 s that a provider couldn't be reached, so it must not
// start before the stand-in models listen: a page that asks who would answer before
// then sends every answer in the next 30 s to the local model.
import { connect } from "node:net";

/** The ports the stand-in models listen on, from their URLs in `env`. */
export function stubPorts(env) {
  return Object.entries(env)
    .filter(([name]) => /^SECUREFOLIO_STUB_[A-Z]+_URL$/.test(name))
    .map(([, url]) => Number(new URL(url).port));
}

function listening(port) {
  return new Promise((resolve) => {
    const socket = connect(port, "127.0.0.1");
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
  });
}

/** Resolves once every port takes a connection; rejects after `seconds`. */
export async function waitForPorts(ports, seconds = 60) {
  const until = Date.now() + seconds * 1000;
  for (const port of ports) {
    while (!(await listening(port))) {
      if (Date.now() > until) {
        throw new Error(`Nothing listens on port ${port} after ${seconds} s`);
      }
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
}

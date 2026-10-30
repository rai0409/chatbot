import { useState } from "react";

const KEY = "console.actorName";

function read(): string {
  try {
    return window.localStorage.getItem(KEY) ?? "";
  } catch {
    return "";
  }
}

/** The name of the person using the console, kept in this browser when it allows (the page works without it). */
export function useActorName(): [string, (name: string) => void] {
  const [name, setName] = useState(read);
  function update(next: string) {
    setName(next);
    try {
      window.localStorage.setItem(KEY, next);
    } catch {
      /* storage is not available: the name is kept for this visit only */
    }
  }
  return [name, update];
}

export async function apiFetch<T>(input: string, init?: RequestInit): Promise<T> {
  const apiUrl = import.meta.env.VITE_API_URL || "";
  const url = apiUrl ? `${apiUrl}${input}` : input;
  
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}

export async function apiFormDataFetch<T>(input: string, body: FormData): Promise<T> {
  const apiUrl = import.meta.env.VITE_API_URL || "";
  const url = apiUrl ? `${apiUrl}${input}` : input;
  
  const response = await fetch(url, {
    method: "POST",
    body,
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with status ${response.status}`);
  }

  return (await response.json()) as T;
}

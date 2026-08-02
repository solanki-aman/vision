const API = "https://api.x.ai/v1";

function key() {
  const k = process.env.XAI_API_KEY;
  if (!k) throw new Error("XAI_API_KEY is not set");
  return k;
}

export async function generateImage(prompt: string, quality = false) {
  const res = await fetch(`${API}/images/generations`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${key()}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      model: quality ? "grok-imagine-image-quality" : "grok-imagine-image",
      prompt,
      n: 1,
      response_format: "url",
    }),
  });
  if (!res.ok) throw new Error(`image generation failed: ${res.status} ${await res.text()}`);
  const json = (await res.json()) as { data: { url: string }[] };
  const url = json.data?.[0]?.url;
  if (!url) throw new Error("image generation returned no url");
  return url;
}

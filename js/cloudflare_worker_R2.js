export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const key = url.pathname.replace(/^\/+/, "");

    if (!key) {
      return new Response("Bad Request", { status: 400 });
    }

    switch (request.method) {
      case "PUT": {
        if (request.headers.get("X-Auth-Token") !== env.X_AUTH_TOKEN) {
          return new Response("Unauthorized", { status: 401 });
        }

        const contentType =
          request.headers.get("Content-Type") || "application/octet-stream";

        await env.R2.put(key, request.body, {
          httpMetadata: {
            contentType,
          },
        });

        return new Response(`Put ${key} successfully!`, { status: 200 });
      }

      case "GET": {
        const object = await env.R2.get(key);

        if (!object) {
          return new Response("Not Found", { status: 404 });
        }

        const headers = new Headers();
        object.writeHttpMetadata(headers);
        headers.set("etag", object.httpEtag);
        headers.set("cache-control", "public, max-age=31536000, immutable");

        return new Response(object.body, {
          status: 200,
          headers,
        });
      }

      default:
        return new Response("Method Not Allowed", {
          status: 405,
          headers: {
            Allow: "PUT, GET",
          },
        });
    }
  },
};

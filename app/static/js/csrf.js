(() => {
  const token = document.querySelector('meta[name="csrf-token"]')?.content;
  if (!token) return;
  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (input, options = {}) => {
    const url = new URL(input instanceof Request ? input.url : input, window.location.href);
    const method = (options.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
    const sameOrigin = url.origin === window.location.origin;
    if (sameOrigin && !["GET", "HEAD", "OPTIONS"].includes(method)) {
      const headers = new Headers(options.headers || (input instanceof Request ? input.headers : {}));
      headers.set("X-CSRF-Token", token);
      options = { ...options, headers };
    }
    const response = await nativeFetch(input, options);
    if (sameOrigin && response.status === 401 && !document.querySelector("#auth-expired")) {
      const notice = document.createElement("p");
      notice.id = "auth-expired";
      notice.className = "error auth-expired";
      notice.setAttribute("role", "alert");
      notice.append("Your recruiter session has expired. ");
      const link = document.createElement("a");
      link.href = "/login";
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = "Sign in again";
      notice.append(link, ", then refresh this page. An active interview can continue.");
      document.body.prepend(notice);
    }
    return response;
  };
})();

# Internal API contract

The public browser only communicates with the Express API. The API derives a user ID from a verified access token and signs a short-lived internal token before calling the Python service. The Python service validates the token and accepts only the supplied, server-fetched financial records. No browser-provided user ID is accepted by either service.

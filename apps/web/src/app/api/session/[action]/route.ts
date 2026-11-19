import { handleSessionRequest } from "../../../../server/session";
import { serverConfig } from "../../../../server/config";

// Session state lives in a per-request cookie: never cache or prerender.
export const dynamic = "force-dynamic";

export async function POST(
  request: Request,
  context: { params: Promise<{ action: string }> },
): Promise<Response> {
  const { action } = await context.params;
  const config = serverConfig();
  return handleSessionRequest(action, request, {
    gatewayUrl: config.gatewayUrl,
    secureCookies: config.secureCookies,
  });
}

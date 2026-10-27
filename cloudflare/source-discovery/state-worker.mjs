import {DurableObject} from "cloudflare:workers";
import {StateCoordinator, route} from "./state-core.mjs";

export class DiscoveryState extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.core = new StateCoordinator(ctx.storage, {deployment: env.DEPLOYMENT_ID,
      code_sha256: env.EXPECTED_CODE_SHA256, policy_sha256: env.EXPECTED_POLICY_SHA256,
      bootstrap: env.ALLOW_BOOTSTRAP === "true"});
  }
  async fetch(request) { return await this.core.handle(request); }
}

export default {async fetch(request, env) { return await route(request, env); }};

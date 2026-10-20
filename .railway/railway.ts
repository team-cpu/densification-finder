import { defineRailway, github, preserve, project, service, volume } from "railway/iac";

// Replaces railway.json (Config as Code), which Railway stops reading on 2026-12-01.
// Build/deploy settings are copied from that file; the rest mirrors the live
// production state from `railway config pull`.
//
// IMPORTANT: omit = delete. A variable, domain or volume that is not declared here
// is removed by `railway config apply`. A variable added in the dashboard must be
// added to `env` below too. Values stay in Railway (preserve()); none are in git.

export default defineRailway(() => {
  const densificationFinderVolume = volume("densification-finder-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "asia-southeast1-eqsg3a", sizeMB: 5000 });
  const densificationFinder = service("densification-finder", {
    source: github("team-cpu/densification-finder", { branch: "master", checkSuites: false }),
    // from railway.json "build"
    build: { builder: "DOCKERFILE", dockerfilePath: "Dockerfile" },
    // from railway.json "deploy". restartPolicyType ON_FAILURE is Railway's
    // default and is not stored by the API (reads back null), so it is omitted
    // to keep `railway config plan` clean; effective policy: ON_FAILURE, 5 retries.
    deploy: {
      healthcheckPath: "/_stcore/health",
      healthcheckTimeout: 120,
      restartPolicyMaxRetries: 5,
    },
    replicas: { "asia-southeast1-eqsg3a": 1 },
    domains: ["scope.normiq.ai"],
    volumeMounts: { "/data": densificationFinderVolume },
    env: { APP_PASSWORD: preserve(), SCOPE_PLANNING_AUTO_MUNICIPAL: preserve(), SCOPE_PLANNING_PREVIEW: preserve(), SCOPE_PLANNING_REFRESH_ENABLED: preserve(), SCOPE_PLANNING_REFRESH_KINDS: preserve(), SCOPE_PLANNING_REFRESH_MAX_PAGES: preserve(), SCOPE_PLANNING_REFRESH_MUNICIPALITIES: preserve(), SCOPE_PLANNING_REUSE_AUTHORIZED: preserve(), SCOPE_PLANNING_REUSE_REFERENCE: preserve() },
  });

  return project("blissful-presence", {
    resources: [densificationFinder, densificationFinderVolume],
  });
});

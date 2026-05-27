/**
 * Minimal semver comparison for the update banner.
 *
 * We only need "is the latest GitHub release newer than the version
 * bundled in this .oxt?". A leading `v` is tolerated (GitHub tags are
 * `vX.Y.Z`). A version carrying a prerelease suffix (`1.2.3-alpha.1`)
 * sorts BELOW the same core release (`1.2.3`), per semver.
 */

interface ParsedVersion {
  core: number[];
  prerelease: string | null;
}

function parse(version: string): ParsedVersion {
  const cleaned = version.trim().replace(/^v/i, '');
  const [core, prerelease = null] = cleaned.split('-', 2);
  const parts = core.split('.').map((p) => {
    const n = parseInt(p, 10);
    return Number.isNaN(n) ? 0 : n;
  });
  return { core: parts, prerelease };
}

/**
 * Returns >0 if `a` is newer than `b`, <0 if older, 0 if equal.
 */
export function compareVersions(a: string, b: string): number {
  const pa = parse(a);
  const pb = parse(b);
  const len = Math.max(pa.core.length, pb.core.length);
  for (let i = 0; i < len; i += 1) {
    const diff = (pa.core[i] ?? 0) - (pb.core[i] ?? 0);
    if (diff !== 0) return diff > 0 ? 1 : -1;
  }
  // Equal cores: a release without a prerelease tag outranks one with.
  if (pa.prerelease === null && pb.prerelease === null) return 0;
  if (pa.prerelease === null) return 1;
  if (pb.prerelease === null) return -1;
  if (pa.prerelease === pb.prerelease) return 0;
  return pa.prerelease > pb.prerelease ? 1 : -1;
}

/** True when `latest` is strictly newer than `current`. */
export function isNewer(latest: string, current: string): boolean {
  return compareVersions(latest, current) > 0;
}

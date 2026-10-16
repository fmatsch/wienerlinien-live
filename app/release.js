const REPO = 'fmatsch/wienerlinien-live';

// "1.10.0" > "1.9.2"; pre-release suffixes are ignored.
export function isNewer(latest, current) {
  const a = String(latest).replace(/^v/, '').split(/[.-]/).map((n) => parseInt(n, 10) || 0);
  const b = String(current).replace(/^v/, '').split(/[.-]/).map((n) => parseInt(n, 10) || 0);
  for (let i = 0; i < 3; i++) {
    if ((a[i] || 0) !== (b[i] || 0)) return (a[i] || 0) > (b[i] || 0);
  }
  return false;
}

// Asks GitHub for the latest release and returns { version, url, page } when it is newer than `current`.
export async function latestRelease(current, arch = process.arch) {
  const res = await fetch(`https://api.github.com/repos/${REPO}/releases/latest`, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': `WienerLinienLive/${current}` },
  });
  if (!res.ok) throw new Error(`GitHub ${res.status}`);
  const rel = await res.json();
  const version = String(rel.tag_name || '').replace(/^v/, '');
  if (!isNewer(version, current)) return null;
  const want = `Wiener-Linien-Live-mac-${arch === 'arm64' ? 'arm64' : 'x64'}.dmg`;
  const asset = (rel.assets || []).find((a) => a.name === want);
  return { version, url: asset?.browser_download_url || rel.html_url, page: rel.html_url };
}

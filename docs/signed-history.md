# Keep signed weekly evidence across quarters

An authorised operator can carry a queue's issued reports forward from one quarter to the next. The archive preserves each original HTML file, including its team signature and internal revision. Each quarter has a portable signature and binds the preceding quarter's signed manifest digest. The payload includes the complete series and deliberately contains private weekly case evidence. Restrict it to recipients approved to see the reports.

Generate a dedicated OpenSSH Ed25519 signing key. Keep it outside the archive. The issuer still needs the original team trust store to check current report revisions before exporting them. The recipient pins the issuer's public key through its own trusted channel.

```sh
ssh-keygen -t ed25519 -N '' -f ./history-issuer
telryn history export --series card-support --quarter 2026-Q4 \
  --signing-key history-issuer --public-key history-issuer.pub \
  --out runs/private-history-q4 runs/q4/*/report.html
telryn history verify runs/private-history-q4 --public-key history-issuer.pub
```

Retain the reported SHA-256 checkpoint in the recipient's records. Carry the entire verified prior archive into the next issue with `--previous`. Keep earlier period files and signatures byte-identical. The verifier takes `--expect HEAD_SHA256` for a pinned head and `--extends PRIOR_SHA256` when accepting a later delivery.

```sh
telryn history export --series card-support --quarter 2027-Q1 \
  --previous runs/private-history-q4 \
  --signing-key history-issuer --public-key history-issuer.pub \
  --out runs/private-history-q1 runs/q1/*/report.html
telryn history verify runs/private-history-q1 --public-key history-issuer.pub \
  --extends PRIOR_SHA256
```

Quarters must be consecutive. Each needs at least one signed week, and the manifest shows every absent week. The checker identifies changes to scope, rubric, decision settings, clock and cutoff timing. Manual deadlines and pending policy reviews remain visible. A consistent scope requires the same approved queue and evidence type. Copy the archive, history signer public key and last verified checkpoint when changing systems. The private team key stays in its original trust store. The portable signature authenticates the issuer's frozen snapshot. The recipient's checkpoint protects against accepting an earlier valid delivery as the current one.

#!/usr/bin/env bash
# Clone the fourteen public repositories the README's figures were measured
# against, at the exact commits they were measured at.
#
#     ./benchmarks/fetch-corpus.sh ~/src
#
# Then point benchmarks/corpus.local.json at them and run:
#
#     python benchmarks/dogfood.py benchmarks/corpus.local.json
#
# These are unaffiliated public projects, chosen for a spread of size and
# layout rather than for flattering results. Pinning the commit is what
# makes the README's file counts and timings checkable -- both drift as
# these projects change.
set -euo pipefail

DEST="${1:-$HOME/src}"
mkdir -p "$DEST"

# repo<TAB>commit
REPOS=$(cat <<'LIST'
encode/httpx	b5addb64f0161ff6bfe94c124ef76f6a1fba5254
psf/requests	5460f467b02e49471c0fd6cfc9ca0adab6351f98
pallets/click	36baa15ff831b939a22bc527cd76ce653ef6f66d
pallets/flask	d318b683471101618febed18996405ad26462110
psf/black	8947c48ef2077c3a301b03c1e814dc2e3f78436e
Textualize/rich	9d8f9a372cc5916fd4781fec207ced7ddac2f08f
pydantic/pydantic	f512b087202f58ec90a9d38c9102858567d72440
fastapi/fastapi	49033471594ea5d99a80abdf1043231b7791ee49
sqlfluff/sqlfluff	642e2e4a34a8e8afd36a45debe8bd3ffcf106c37
django/django	73cc09f14f13fedddc14d6ba5b287cb33c24e4a4
python-attrs/attrs	764bf92a1c96abe4615b59f3e5a7b738b0340a94
urllib3/urllib3	85a8a9cfad3398bc504d088233d0a11af219a82a
pytest-dev/pytest	2cd217e5b8842f842bc6e25514db8946b3108b2d
scrapy/scrapy	53eb8d60bcd0160633f6513478f958ed5a457363
LIST
)

while IFS=$'\t' read -r slug commit; do
    [ -z "$slug" ] && continue
    name="${slug##*/}"
    target="$DEST/$name"
    if [ -d "$target/.git" ]; then
        echo "have    $name"
        continue
    fi
    echo "fetch   $name ($commit)"
    # A shallow fetch of one commit: far cheaper than cloning full history,
    # and the pinned commit is the whole point.
    git init -q "$target"
    git -C "$target" remote add origin "https://github.com/$slug.git"
    git -C "$target" fetch -q --depth 1 origin "$commit"
    git -C "$target" checkout -q FETCH_HEAD
done <<< "$REPOS"

echo
echo "Corpus in $DEST. Point benchmarks/corpus.local.json at these paths."

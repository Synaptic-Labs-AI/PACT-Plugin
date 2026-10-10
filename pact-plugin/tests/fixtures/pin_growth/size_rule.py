"""A copy of pin_caps.size_violation whose parts can be switched off one at a
time, for the size rule's mutation tests.

With every switch on it decides as the shipped rule does, which a test checks
over the fixed size rows; each mutant switches one part off.
"""

from collections import Counter

from pin_caps import _size_reason, _violates, _word_pairs


def size_rule(*, share=0.5, heading_edges=True, content_edges=True, max_check=True, sum_check=True,
              orphan_pairing=True, line_links=True, pair_links=True, pair_share=True,
              free_needs_empty_component=True, free_needs_no_successor=True):
    def rule(pre_pins, post_pins):
        bad = [q for q, pin in enumerate(post_pins) if _violates(pin)]
        if not bad:
            return None
        parent = {}

        def find(node):
            while parent.get(node, node) != node:
                node = parent[node]
            return node

        def join(a, b):
            a, b = find(a), find(b)
            if a != b:
                parent[a] = b

        def normal(heading):
            return " ".join(heading.split()).casefold()

        edges, descended, partnered, source = [], set(), set(), {}
        for q, post in enumerate(post_pins):
            for p, pre in enumerate(pre_pins):
                if p not in partnered and normal(pre.heading) == normal(post.heading):
                    partnered.add(p)
                    if heading_edges:
                        edges.append((p, q))
                        source[q] = p
                    descended.add(p)
                    break
        pre_pairs = [_word_pairs(pin) for pin in pre_pins]
        post_pairs = [_word_pairs(pin) for pin in post_pins]
        for q, mine in enumerate(post_pairs):
            size = sum(mine.values())
            if not size:
                continue
            for p, theirs in enumerate(pre_pairs):
                shared = sum((mine & theirs).values())
                if content_edges and shared >= share * size:
                    edges.append((p, q))
                    if q not in source and p not in source.values():
                        source[q] = p
                if sum(theirs.values()) and shared >= share * sum(theirs.values()):
                    descended.add(p)
        for p, q in edges:
            join(("q", q), ("p", p))

        post_lines = [Counter(pin.lines) for pin in post_pins]
        kept_lines = {}
        for q, lines in enumerate(post_lines):
            kept_lines.setdefault(find(("q", q)), Counter()).update(lines)
        links = []
        if line_links:
            for p, pin in enumerate(pre_pins):
                root = find(("p", p))
                lost = Counter(pin.lines) - kept_lines.get(root, Counter())
                if lost:
                    links.extend((p, q) for q, lines in enumerate(post_lines)
                                 if find(("q", q)) != root and lines & lost)
        pairs_before, pairs_after, first_pre = {}, {}, {}
        for p, pairs in enumerate(pre_pairs):
            root = find(("p", p))
            pairs_before.setdefault(root, Counter()).update(pairs)
            first_pre.setdefault(root, p)
        for q, pairs in enumerate(post_pairs):
            pairs_after.setdefault(find(("q", q)), Counter()).update(pairs)
        lost_pairs = {root: pairs - pairs_after.get(root, Counter()) for root, pairs in pairs_before.items()}
        if pair_links:
            for q, pairs in enumerate(post_pairs):
                root = find(("q", q))
                gained = pairs - pairs_before.get(root, Counter())
                count = sum(gained.values())
                if not count:
                    continue
                for other, lost in lost_pairs.items():
                    if other == root or not lost:
                        continue
                    overlap = sum((gained & lost).values())
                    if (2 * overlap >= count) if pair_share else overlap:
                        links.append((first_pre[other], q))
        for p, q in links:
            join(("q", q), ("p", p))

        components = {}
        for p, pin in enumerate(pre_pins):
            if _violates(pin):
                components.setdefault(find(("p", p)), ([], []))[0].append(pin.body_chars)
        for q in bad:
            components.setdefault(find(("q", q)), ([], []))[1].append(q)
        orphans = []
        for before, after in components.values():
            if not after:
                continue
            sizes = [post_pins[q].body_chars for q in after]
            if not before:
                orphans.extend(after)
            elif (max_check and max(sizes) > max(before)) or (sum_check and sum(sizes) > sum(before)):
                return _size_reason(pre_pins, post_pins, source, after, before)
        taken = {root for root, (_, after) in components.items() if after}
        free = sorted(pin.body_chars for p, pin in enumerate(pre_pins)
                      if _violates(pin)
                      and (not free_needs_no_successor or p not in descended)
                      and (not free_needs_empty_component or find(("p", p)) not in taken))
        for q in sorted(orphans, key=lambda q: post_pins[q].body_chars, reverse=True):
            chars = post_pins[q].body_chars
            fit = next((size for size in free if size >= chars), None) if orphan_pairing else None
            if fit is None:
                return _size_reason(pre_pins, post_pins, source, [q], [])
            free.remove(fit)
        return None
    return rule

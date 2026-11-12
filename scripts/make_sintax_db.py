#!/usr/bin/env python
"""
Convert a UNITE General FASTA release into the SINTAX format VSEARCH expects.

Use the *eukaryote* release, not the fungi-only one. Coastal and mangrove ITS
libraries carry a large fraction of plant, algal, metazoan and alveolate
amplicons; with a fungi-only reference those are forced onto the nearest fungal
name instead of being recognised and discarded.

UNITE header
  >Abisia_spuria|JN655624|SH1140246.10FU|refs|k__Fungi;p__Ascomycota;...;s__Abisia_spuria
SINTAX header
  >SH1140246.10FU;tax=d:Fungi,p:Ascomycota,...,s:Abisia_spuria;

Placeholder names (unidentified, Incertae_sedis, *_sp, GS-codes, and the
_fam_/_gen_/_ord_/_cls_/_phy_ Incertae-sedis forms) are dropped so that SINTAX
bootstraps are not spent on uninformative labels.

Usage
-----
python make_sintax_db.py sh_general_release_dynamic_all_<date>.fasta unite_sintax.fasta
"""

import re
import sys
from collections import Counter

RANKS = {'k': 'd', 'p': 'p', 'c': 'c', 'o': 'o', 'f': 'f', 'g': 'g', 's': 's'}
DROP = re.compile(r'_sp$|unidentified|Incertae_sedis|^GS[0-9]|_fam_|_gen_|_ord_|_cls_|_phy_',
                  re.IGNORECASE)


def convert(src, dst):
    kingdoms, n_seq, n_untaxed = Counter(), 0, 0
    with open(src) as fin, open(dst, 'w') as fout:
        for line in fin:
            if not line.startswith('>'):
                fout.write(line)
                continue
            n_seq += 1
            parts = line[1:].rstrip('\n').split('|')
            sh = parts[2] if len(parts) >= 3 else parts[0]
            lineage = parts[-1]
            kingdoms[re.sub(r'^k__', '', lineage.split(';')[0])] += 1
            fields = []
            for el in lineage.split(';'):
                pre, name = el[:1], re.sub(r'^[a-z]__', '', el)
                if pre in RANKS and name and not DROP.search(name):
                    fields.append(f"{RANKS[pre]}:{name.replace(',', '_')}")
            if not fields:
                n_untaxed += 1
            fout.write(f">{sh};tax={','.join(fields)};\n")
    print(f"sequences written : {n_seq}")
    print(f"without usable taxonomy: {n_untaxed}")
    print("kingdom composition:")
    for k, v in kingdoms.most_common(12):
        print(f"   {k:32s} {v}")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(sys.argv[1], sys.argv[2])

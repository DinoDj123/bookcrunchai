# Test books

Public domain in the USA; Project Gutenberg files are distributed under the Project Gutenberg
licence (the parser removes the licence header and footer). Safe to commit.

| File | Title | Source | Downloaded | Size (bytes) | SHA-256 |
|---|---|---|---|---|---|
| `pride-and-prejudice.epub` | Pride and Prejudice, Jane Austen (Project Gutenberg #1342) | https://gutenberg.pglaf.org/cache/epub/1342/pg1342.epub | 23.11.2026 | 609131 | `cb0d439ee71c4a14f18249759ad5bf1f753b201a298ccb0b10b53536bf5818b6` |
| `meditations.epub` | Meditations, Marcus Aurelius (Project Gutenberg #2680) | https://gutenberg.pglaf.org/cache/epub/2680/pg2680-images-3.epub | 23.11.2026 | 454997 | `62c71326bea242893ce8c3ce54bdf5fad3989a827a1462ec03f7dadf8bf5bc89` |
| `the-prince.pdf` | The Prince, Nicolo Machiavelli (Planet eBook PDF, 194 pages) | https://www.planetebook.com/free-ebooks/the-prince.pdf | 23.11.2026 | 854178 | `49898682ff90ec6ebc574386e0925ee5f431360673f954032dea4f9131c13e40` |

Notes:

- gutenberg.org was down for maintenance (HTTP 503) on 23.11.2026, so both files come from the
  official Project Gutenberg mirror run by PGLAF (`gutenberg.pglaf.org`, same `cache/epub/<id>/`
  layout as gutenberg.org).
- Pride and Prejudice: the "EPUB3 with images" variant is 24.9 MB (Hugh Thomson illustrations),
  over the 15 MB limit of the plan, so the no-images variant is used (decided by Dino). It is
  EPUB 2 with an NCX table of contents, so the EPUB2 path is tested on a real book too.
- Meditations: "EPUB3 with images" variant (EPUB 3 nav document + NCX).
- The Prince: Planet eBook edition (W. K. Marriott translation), a text-based PDF with
  322×484 pt pages, a running footer on every page and a PDF outline; used for the PDF reader.

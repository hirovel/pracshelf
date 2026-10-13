# Astro Cactus source provenance

Source: https://github.com/chrismwilliams/astro-theme-cactus
Commit: 728f1f4eeb80208398649261d7635f311cafa011
License: MIT, Copyright (c) 2022 Chris Williams. Full text in LICENSE.

Retained upstream source: Header, Footer, ThemeToggle, ThemeProvider, SkipLink, domElement, global.css and style dependencies, tailwind.config.ts. The active Base layout now uses a locally authored terminal shell and terminal.css. The upstream header/footer/theme controls are retained for provenance but no longer rendered. SkipLink, Tailwind setup, typography and foundational CSS remain in use in workbench pages. The private catalogue no longer uses PostPreview-derived markup.

The dashboard, candidates page and their UI scripts live in frontend/src. Workbench markup and runtime scripts remain in src/learning_library. Astro builds the pages; FastAPI serves compiled pages and data APIs from the same origin. No Astro server is required at runtime. Local terminal styling is an adaptation, not an upstream Cactus design.

## Private article reader: Maxime Heckel reference

Source: https://github.com/MaximeHeckel/blog.maximeheckel.com (revision ccbb3def76870a1b8feaa4506c5877d24fd7bdfd, inspected 2026-10-11).
Code license: MIT, Copyright (c) 2024 Maxime Heckel; full notice in licenses/MaximeHeckel.MIT.txt, included with the compiled assets. Pinned source URLs, revision and SHA256 fingerprints are captured in ../.scratch/maxime-fidelity/upstream/. Earlier exploratory references remain in ../.scratch/learning-reader/upstream-maxime/.

The reader now ports the upstream BlogPost/Headline typography and spacing, Inter and Departure Mono font setup, GlassMaterial values, Dock dimensions, DynamicTOC expansion and code styling. Dark color and font tokens follow the upstream @maximeheckel/design-system 4.2.3 package (declared MIT); release metadata and package integrity are in licenses/MaximeHeckelDesignSystem.source.json. The package was inspected, not installed as a React dependency.

The adapted layout and stylesheet live in frontend/src/layouts/ReadingLayout.astro and frontend/src/styles/reading-blog.css; frontend/src/pages/reader.astro provides the article markup. frontend/src/scripts/dock.js implements the shared navigation highlight, keyboard movement and scroll-to-top. frontend/src/scripts/reading-polish.js implements article scroll navigation and code actions. The existing Python Library.article Interface selects saved source-version-matched results; src/learning_library/reader.js renders them without executing model tasks. Notes use the existing optional editor and explicit save action.

The private catalogue at / also uses ReadingLayout, with locally authored index.astro, library.css and library.js. Its filtering/date projection lives in library-view.js. It borrows the reader's visual tokens and navigation material rather than porting the upstream homepage, and does not claim an identical upstream homepage. Search, categories and filters read the existing Library.list API; no upstream editorial content or statistics are copied.

Inter (normal and italic), Fira Code and Departure Mono files are copied unchanged from the pinned upstream font directory and bundled locally by Astro. Their individual SIL OFL notices are in licenses/Inter.OFL.txt, FiraCode.OFL.txt and DepartureMono.OFL.txt; file hashes and URLs are in ../.scratch/maxime-fidelity/upstream/font-sources.json. Prism 1.30.0 provides lexical highlighting, under licenses/Prism.MIT.txt; source text is unchanged. No external font service is used at runtime.

This is an Astro adaptation, not the upstream Next.js/MDX application or a full theme import. No upstream article, image, logo or 3D asset is included. Upstream content and images have a separate CC BY-NC 4.0 license. Codemode is separately saved source material, not Maxime Heckel content. The prototype and previous reader source are archived in ../docs/prototypes/maxime-reader-2026-10-11/.

Pixel comparisons use the same viewport and compare measured typography and dimensions. Article text, local navigation labels, provenance, core controls and bilingual mode differ from the upstream content, so the complete page is not claimed to be an identical screenshot. See ../docs/maxime-fidelity-2026-10-11.md for measured results and retained functional differences.

The independent reading site in reading/ uses the same installed Astro dependency, with locally authored templates and a minimal light reading stylesheet. It does not import the workbench layout or scripts. Its output and build records stay in separate data/reading-builds directories; no upstream theme authorship is claimed for these new pages.

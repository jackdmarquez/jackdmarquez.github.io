# jackdmarquez.github.io

Personal website of Jack Marquez, published at https://jackdmarquez.github.io/.

Built with Jekyll on the [academicpages](https://github.com/academicpages/academicpages.github.io) template (a fork of [Minimal Mistakes](https://mmistakes.github.io/minimal-mistakes/), MIT license).

## Updating content

- Home/bio: `_pages/about.md`
- Publications: `_publications/*.md` (synced automatically from ORCID by `.github/workflows/sync-orcid-publications.yml`)
- CV: replace `files/jack_CV.pdf`
- Site settings and author sidebar: `_config.yml`; menu: `_data/navigation.yml`

## Run locally

```bash
bundle install
bundle exec jekyll serve -l -H localhost
```

Then open http://localhost:4000.

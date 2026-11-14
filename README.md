# foodmeta-data

Data feed for the FoodMeta UAE app.

An hourly GitHub Actions job (`.github/workflows/scrape.yml`) runs `scraper/scrape.py`,
which reads restaurant menus from talabat.com (UAE) and publishes:

- `data/meals.json` — array of meals in the app's format
  (`id, name, restaurant, provider, priceAed, deliveryFeeAed, rating, cuisine, imageUrl, deepLink, canonicalId`)
- `data/meta.json` — when the data was last refreshed, counts, areas covered, errors

The app downloads:

    https://raw.githubusercontent.com/amrsoliman-blob/foodmeta-data/main/data/meals.json

Areas and limits can be changed with the `AREAS`, `MAX_RESTAURANTS_PER_AREA` and
`MAX_ITEMS_PER_RESTAURANT` environment variables in the workflow.

Keeta is not included: it has no public web menu in the UAE (app-only).

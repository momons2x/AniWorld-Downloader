"""Read a Miruro series page: metadata, seasons and episode counts.

Miruro has no separate season URLs - one /info/<id>/<slug> page covers the
whole title, and the episode URL is built from the same media id.
"""

from aniworld.models import MiruroSeries

url = (
    "https://miruro.tv/info/P6joTcOcZ5MAg745EfyST5b3IZR8Vs9w/"
    "even-the-student-council-has-its-holes"
)

series = MiruroSeries(url)

print("=== SERIES INFO ===")
print("URL:", series.url)
print("Media ID:", series.media_id)
print("Title:", series.title)
print("Title Clean:", series.title_cleaned)
print("Description:", series.description)
print("Genres:", series.genres)
print("Release year:", series.release_year)
print("Poster URL:", series.poster_url)
print("IMDB ID:", series.imdb)
print("Episodes:", series.episode_count)
print("Seasons:", series.season_count)

for season in series.seasons:
    print(f"\nSeason {season.season_number}: {season.episode_count} episodes")
    print("Languages per episode:", season.episode_languages)
    for episode in season.episodes[:3]:
        print(f"  E{episode.episode_number:03d} {episode.title_en}")

print()
print("# series.download()  # save every episode")
print("# series.watch()      # open in the configured player")

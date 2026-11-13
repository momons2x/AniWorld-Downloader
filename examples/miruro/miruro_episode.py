"""Read one Miruro episode, show what it offers, and resolve its stream URL."""

from aniworld.models import MiruroEpisode

url = (
    "https://miruro.tv/watch/P6joTcOcZ5MAg745EfyST5b3IZR8Vs9w/"
    "even-the-student-council-has-its-holes/episode/1"
)

episode = MiruroEpisode(url)

print("=== EPISODE INFO ===")
print("URL:", episode.url)
print("Episode:", episode.episode_number)
print("Title:", episode.title_en)
print("Language:", episode.language)
print("Already downloaded:", episode.is_downloaded["exists"])
print("Would be saved to:", episode._episode_path)
print("Skip times:", episode.skip_times)
print()
print("Stream providers:")
print(episode.provider_data)
print()
print("Stream URL:", episode.stream_url)

# episode.download()  # save it to the configured download folder

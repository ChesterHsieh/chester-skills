"""lolreplay - parse League of Legends .rofl replay files (ROFL2) and analyse them.

Layers:
  container  -> header, frame directory, zstd decompression
  metadata   -> tail JSON (game length, per-player stats)
  blocks     -> packet stream inside frames (time / type / net_id / content)
  calibrate  -> identify packet types by matching per-player counts to metadata stats
  timeline   -> deaths, level-ups, shop events, activity, fights
  analysis   -> derived metrics, comparisons, flags
  report     -> markdown / json rendering
"""
__version__ = "0.1.0"

"""Shared helpers for turning birdsongs/ filenames into display names."""

import re

# Filename-safe title-cased name -> correct display name (apostrophe restored)
APOSTROPHE_NAMES = {
    "Bewicks Wren": "Bewick's Wren",
    "Swainsons Thrush": "Swainson's Thrush",
    "Townsends Solitaire": "Townsend's Solitaire",
    "Lincolns Sparrow": "Lincoln's Sparrow",
    "Cassins Finch": "Cassin's Finch",
    "Wilsons Warbler": "Wilson's Warbler",
    "Macgillivrays Warbler": "MacGillivray's Warbler",
    "Townsends Warbler": "Townsend's Warbler",
    "Cassins Vireo": "Cassin's Vireo",
    "Huttons Vireo": "Hutton's Vireo",
    "Says Phoebe": "Say's Phoebe",
    "Stellers Jay": "Steller's Jay",
    "Clarks Nutcracker": "Clark's Nutcracker",
    "Annas Hummingbird": "Anna's Hummingbird",
    "Bullocks Oriole": "Bullock's Oriole",
    "Brewers Blackbird": "Brewer's Blackbird",
}


def species_slug_from_filename(filename):
    """Turn 'bewicks_wren_2.mp3' into 'bewicks_wren' (drops the candidate-clip index)."""
    return re.sub(r"_\d+\.mp3$", "", filename)


def bird_name_from_filename(filename):
    """Turn 'bewicks_wren_2.mp3' into 'Bewick's Wren'."""
    name = species_slug_from_filename(filename).replace("_", " ").title()
    return APOSTROPHE_NAMES.get(name, name)

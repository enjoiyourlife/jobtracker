"""
A curated list of US tech-hub cities, for the Settings page's city
autocomplete only — not used anywhere in scoring or filtering.
Typing "seattle, bel" and seeing Bellevue offered beats having to know
a metro area's satellite-city names off the top of your head.
"""

from __future__ import annotations

US_TECH_CITIES: tuple[str, ...] = (
    "Seattle", "Bellevue", "Redmond", "Kirkland", "Tacoma", "Everett",
    "Portland", "Beaverton", "Hillsboro", "Lake Oswego",
    "San Francisco", "Oakland", "San Jose", "Mountain View", "Palo Alto",
    "Sunnyvale", "Santa Clara", "Menlo Park", "Redwood City", "Cupertino",
    "Los Angeles", "San Diego", "Irvine", "Pasadena", "Santa Monica",
    "Burbank", "El Segundo", "Culver City", "Long Beach", "Sacramento",
    "Orlando", "Tampa", "Miami", "Jacksonville", "Fort Lauderdale",
    "Austin", "Dallas", "Houston", "San Antonio",
    "Denver", "Boulder",
    "Chicago",
    "Boston", "Cambridge",
    "New York", "Brooklyn", "Jersey City",
    "Atlanta",
    "Phoenix", "Scottsdale", "Tempe",
    "Nashville",
    "Raleigh", "Durham", "Charlotte",
    "Washington DC", "Arlington", "Reston", "McLean",
    "Philadelphia",
    "Minneapolis",
    "Detroit", "Ann Arbor",
    "Salt Lake City",
    "Las Vegas",
    "Columbus",
    "Indianapolis",
    "Pittsburgh",
    "Cincinnati",
    "Kansas City",
    "St. Louis",
    "Baltimore",
    "Remote", "Remote - US", "United States",
)

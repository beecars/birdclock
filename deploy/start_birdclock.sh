#!/bin/sh
cd ~/birdclock/deploy
~/birdclock/.venv/bin/python birdclock.py &
~/birdclock/.venv/bin/python birdclock_web.py &
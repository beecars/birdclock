#!/bin/sh
cd ~/birdclock/deploy
python3 birdclock.py &
python3 birdclock_web.py &
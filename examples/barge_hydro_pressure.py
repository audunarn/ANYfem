"""Run with an environment containing the three ANY owner packages."""
import argparse
from anyfem.application.hydro_wave_pressure import map_barge_regular_wave

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('output_directory')
    args = parser.parse_args()
    _, _, report = map_barge_regular_wave(args.output_directory)
    print(report)

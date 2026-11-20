# SeatRace

Eight students click “book” for the final robotics lab seat. SeatRace confirms one claim, queues the others, then promotes the oldest waiting ticket on cancellation. The concurrency test opens eight independent database connections and releases them together.

## Run it

Python 3.10+ with SQLite. From this folder:

```sh
python seat_race.py race
python seat_race.py race --students 12 --seats 3
python seat_race.py demo
python -m unittest discover -v
```

The race opens one SQLite connection per student, then releases the workers together. Its JSON report starts with the measured counts, shows the queue before and after the cancellation, and verifies five booking invariants. The winning student can change between runs. Try more seats than students to see a cancellation with an empty queue. Each option accepts 1–24.

Both experiments use temporary databases. The scripted demo gives Mira the seat, queues Dev and Ari, and promotes Dev when Mira cancels. For a persistent queue of your own:

```sh
python seat_race.py create robotics-friday 1
python seat_race.py book robotics-friday Mira
python seat_race.py book robotics-friday Dev
python seat_race.py status robotics-friday
python seat_race.py cancel robotics-friday Mira
```

Pass `--db other.sqlite3` before the command to use another file. Running the same booking twice returns the existing state.

## Interview walkthrough

The write transaction starts before the capacity check, so a second writer sees the first confirmed booking. The database also enforces a single booking per student and slot. A cancellation and any promotion commit together. Ticket order means order of accepted writes, which can differ from the order people clicked.

**Trade-off.** SQLite serializes writers. This is a local, single-database demonstration. It uses typed student labels with no identity verification, notifications, or deployment layer.

[SQLite transaction semantics](https://www.sqlite.org/lang_transaction.html)

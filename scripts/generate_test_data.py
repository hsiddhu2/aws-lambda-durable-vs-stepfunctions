#!/usr/bin/env python3
import csv, os, random, argparse
from datetime import datetime, timedelta

def generate_csv(filepath, num_records=100):
    products = ["Widget A", "Widget B", "Gadget X", "Gadget Y", "Tool Z"]
    regions = ["US-East", "US-West", "EU-West", "AP-South", "AP-East"]
    with open(filepath, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "name", "date", "amount", "quantity", "region"])
        for i in range(1, num_records + 1):
            date = datetime(2025, 1, 1) + timedelta(days=random.randint(0, 365))
            writer.writerow([str(i), random.choice(products), date.strftime("%Y-%m-%d"),
                           round(random.uniform(10, 500), 2), random.randint(1, 100), random.choice(regions)])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--records", type=int, default=100)
    parser.add_argument("--output-dir", default="test-data")
    args = parser.parse_args()
    os.makedirs(args.output_dir, exist_ok=True)
    for i in range(args.count):
        generate_csv(os.path.join(args.output_dir, f"data_{i+1:04d}.csv"), args.records)
    print(f"Generated {args.count} CSV files in {args.output_dir}/")

if __name__ == "__main__":
    main()

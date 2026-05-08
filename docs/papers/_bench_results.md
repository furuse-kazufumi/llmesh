# LLMesh Industrial — Serialization & Pipeline Benchmarks

Repeats:  5  | Workload sizes:  (1000, 10000, 100000, 1000000)

| Operation | n | Median (µs) | Throughput (items/s) |
|-----------|---:|------------:|---------------------:|
| PointCloud.to_bytes(1,000) | 1,000 | 256.3 | 3,901,678 |
| PointCloud.from_bytes(1,000) | 1,000 | 156.3 | 6,397,953 |
| encode_dvs_events(1,000) | 1,000 | 249.9 | 4,001,601 |
| decode_dvs_events(1,000) | 1,000 | 984.2 | 1,016,054 |
| PointCloud.to_bytes(10,000) | 10,000 | 2,447.5 | 4,085,802 |
| PointCloud.from_bytes(10,000) | 10,000 | 2,042.0 | 4,897,160 |
| encode_dvs_events(10,000) | 10,000 | 2,600.5 | 3,845,414 |
| decode_dvs_events(10,000) | 10,000 | 10,715.6 | 933,219 |
| PointCloud.to_bytes(100,000) | 100,000 | 24,881.0 | 4,019,131 |
| PointCloud.from_bytes(100,000) | 100,000 | 23,234.1 | 4,304,019 |
| encode_dvs_events(100,000) | 100,000 | 32,602.2 | 3,067,278 |
| decode_dvs_events(100,000) | 100,000 | 121,097.1 | 825,784 |
| PointCloud.to_bytes(1,000,000) | 1,000,000 | 249,040.1 | 4,015,418 |
| PointCloud.from_bytes(1,000,000) | 1,000,000 | 272,393.7 | 3,671,157 |
| encode_dvs_events(1,000,000) | 1,000,000 | 296,437.3 | 3,373,395 |
| decode_dvs_events(1,000,000) | 1,000,000 | 1,439,272.4 | 694,796 |
| IndustrialPipeline.process+CUSUM (1,000) | 1,000 | 5,160.5 | 193,780 |
| IndustrialPipeline.process+CUSUM (10,000) | 10,000 | 54,064.3 | 184,965 |

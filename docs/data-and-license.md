# Data and licensing

The sample inputs and test fixtures are synthetic files for checking capture, comparison, deduplication, and restore. They contain no personal documents, customer data, or production backups.

The baseline runs locally and doesn't contact a service. A vault stores file contents, relative paths, source paths, labels, and metadata. Contents are not encrypted. Nothing is excluded by default, so check the source folder and exclusion patterns before capturing it.

No third-party runtime packages are required by the baseline. Python and its bundled SQLite implementation are provided by the user's Python installation. Planned portable archives will use standard-library archive and compression modules.

The target five-process system will use loopback or controlled local message queues and separate node directories on one supported desktop. It requires no external accounts, internet, cloud service, GPU, Docker, sound, or Windows-only dependency. Protocol references are reading material, not runtime services.

Authentication, encryption, key management, hostile peers, and Byzantine behavior are outside scope. The reference cluster is a trusted local environment. Its separate directories do not establish independent physical storage or protection against whole-machine loss.

The project is distributed under the [MIT license](../LICENSE). A license for the tool does not grant rights to files someone chooses to back up. Only copy, publish, or share data you have permission to use.

Generated vaults, restored files, temporary test outputs, and local settings do not belong in this repository. Keep real backup data outside the checkout.

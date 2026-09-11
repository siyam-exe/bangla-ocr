using System;
using System.Collections.Generic;
using System.Management;
using System.Text;
using System.Web.Script.Serialization;

namespace BanglaOcr.Windows
{
    internal sealed class HardwareReport
    {
        public string recommendation { get; set; }
        public string[] default_runtimes { get; set; }
        public string[] detected_adapters { get; set; }
        public string reason { get; set; }
        public bool detection_succeeded { get; set; }
    }

    internal static class Program
    {
        private static int Main(string[] args)
        {
            Console.OutputEncoding = new UTF8Encoding(false);
            bool classifyOnly = false;
            bool installerOutput = false;
            List<string> adapters = new List<string>();

            for (int index = 0; index < args.Length; index++)
            {
                if (string.Equals(args[index], "--classify", StringComparison.OrdinalIgnoreCase))
                {
                    classifyOnly = true;
                }
                else if (string.Equals(args[index], "--installer", StringComparison.OrdinalIgnoreCase))
                {
                    installerOutput = true;
                }
                else if (string.Equals(args[index], "--adapter", StringComparison.OrdinalIgnoreCase) && index + 1 < args.Length)
                {
                    adapters.Add(args[++index]);
                }
                else
                {
                    Console.Error.WriteLine("Usage: hardware-probe.exe [--installer] [--classify] [--adapter NAME]");
                    return 2;
                }
            }

            bool detectionSucceeded = true;
            if (!classifyOnly)
            {
                try
                {
                    adapters = ReadVideoControllerNames();
                }
                catch (Exception exception)
                {
                    detectionSucceeded = false;
                    Console.Error.WriteLine(exception.Message);
                    adapters.Clear();
                }
            }

            HardwareReport report = Classify(adapters, detectionSucceeded);
            if (installerOutput)
            {
                Console.WriteLine("recommendation=" + report.recommendation);
                Console.WriteLine("adapters=" + string.Join(" | ", report.detected_adapters));
                Console.WriteLine("reason=" + report.reason);
                Console.WriteLine("detection_succeeded=" + (report.detection_succeeded ? "1" : "0"));
            }
            else
            {
                Console.WriteLine(new JavaScriptSerializer().Serialize(report));
            }
            return 0;
        }

        private static List<string> ReadVideoControllerNames()
        {
            List<string> names = new List<string>();
            using (ManagementObjectSearcher searcher = new ManagementObjectSearcher("SELECT Name FROM Win32_VideoController"))
            using (ManagementObjectCollection results = searcher.Get())
            {
                foreach (ManagementObject adapter in results)
                {
                    object value = adapter["Name"];
                    AddUnique(names, value == null ? string.Empty : Convert.ToString(value));
                }
            }
            return names;
        }

        private static HardwareReport Classify(IEnumerable<string> source, bool detectionSucceeded)
        {
            List<string> adapters = new List<string>();
            bool hasNvidia = false;
            bool hasVulkanVendor = false;

            foreach (string value in source)
            {
                string name = (value ?? string.Empty).Trim();
                if (name.Length == 0 || IsSoftwareAdapter(name))
                {
                    continue;
                }
                AddUnique(adapters, name);
                string normalized = name.ToUpperInvariant();
                if (normalized.Contains("NVIDIA"))
                {
                    hasNvidia = true;
                }
                if (normalized.Contains("AMD") || normalized.Contains("RADEON") || normalized.Contains("INTEL"))
                {
                    hasVulkanVendor = true;
                }
            }

            if (hasNvidia)
            {
                return CreateReport(
                    "cuda",
                    new string[] { "cpu", "cuda" },
                    adapters,
                    "NVIDIA graphics detected. CUDA acceleration is recommended and CPU remains available as a fallback.",
                    detectionSucceeded
                );
            }
            if (hasVulkanVendor)
            {
                return CreateReport(
                    "vulkan",
                    new string[] { "cpu", "vulkan" },
                    adapters,
                    "AMD or Intel graphics detected. Vulkan acceleration is experimental and CPU remains available as a fallback.",
                    detectionSucceeded
                );
            }
            return CreateReport(
                "cpu",
                new string[] { "cpu" },
                adapters,
                detectionSucceeded
                    ? "No supported GPU was detected. CPU OCR will be installed and may be slower."
                    : "Hardware detection could not finish. CPU OCR will be installed and may be slower.",
                detectionSucceeded
            );
        }

        private static HardwareReport CreateReport(
            string recommendation,
            string[] runtimes,
            List<string> adapters,
            string reason,
            bool detectionSucceeded)
        {
            return new HardwareReport
            {
                recommendation = recommendation,
                default_runtimes = runtimes,
                detected_adapters = adapters.ToArray(),
                reason = reason,
                detection_succeeded = detectionSucceeded
            };
        }

        private static bool IsSoftwareAdapter(string name)
        {
            string value = name.ToUpperInvariant();
            return value.Contains("MICROSOFT BASIC DISPLAY")
                || value.Contains("REMOTE DISPLAY")
                || value.Contains("RDP DISPLAY")
                || value.Contains("VIRTUAL DISPLAY");
        }

        private static void AddUnique(List<string> values, string candidate)
        {
            string value = (candidate ?? string.Empty).Trim();
            if (value.Length == 0)
            {
                return;
            }
            foreach (string existing in values)
            {
                if (string.Equals(existing, value, StringComparison.OrdinalIgnoreCase))
                {
                    return;
                }
            }
            values.Add(value);
        }
    }
}

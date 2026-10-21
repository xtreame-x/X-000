#include <windows.h>
#include <psapi.h>
#include <tlhelp32.h>
#include <iostream>
#include <vector>
#include <string>
#include <sstream>
#include <iomanip>
#include <algorithm>

#undef UNICODE
#undef _UNICODE

#pragma comment(lib, "psapi.lib")

// ── CPU usage ────────────────────────────────────────────────────────────────

double get_cpu_usage()
{
    static FILETIME prev_idle, prev_kernel, prev_user;
    static bool first = true;

    FILETIME idle, kernel, user;
    if (!GetSystemTimes(&idle, &kernel, &user))
        return -1.0;

    if (first)
    {
        prev_idle = idle;
        prev_kernel = kernel;
        prev_user = user;
        first = false;
        Sleep(200);
        if (!GetSystemTimes(&idle, &kernel, &user))
            return -1.0;
    }

    auto ft_to_ull = [](FILETIME ft) -> unsigned long long
    {
        return (((unsigned long long)ft.dwHighDateTime) << 32) | ft.dwLowDateTime;
    };

    unsigned long long idle_diff = ft_to_ull(idle) - ft_to_ull(prev_idle);
    unsigned long long kernel_diff = ft_to_ull(kernel) - ft_to_ull(prev_kernel);
    unsigned long long user_diff = ft_to_ull(user) - ft_to_ull(prev_user);

    unsigned long long total = kernel_diff + user_diff;
    double usage = total == 0 ? 0.0 : (1.0 - (double)idle_diff / total) * 100.0;

    prev_idle = idle;
    prev_kernel = kernel;
    prev_user = user;

    return usage;
}

// ── RAM ──────────────────────────────────────────────────────────────────────

struct RamInfo
{
    double used_mb;
    double total_mb;
    double percent;
};

RamInfo get_ram()
{
    MEMORYSTATUSEX mem;
    mem.dwLength = sizeof(mem);
    GlobalMemoryStatusEx(&mem);

    RamInfo r;
    r.total_mb = mem.ullTotalPhys / (1024.0 * 1024.0);
    r.used_mb = (mem.ullTotalPhys - mem.ullAvailPhys) / (1024.0 * 1024.0);
    r.percent = (r.used_mb / r.total_mb) * 100.0;
    return r;
}

// ── Disk ─────────────────────────────────────────────────────────────────────

struct DiskInfo
{
    double used_gb;
    double total_gb;
    double percent;
};

DiskInfo get_disk()
{
    ULARGE_INTEGER free_bytes, total_bytes, total_free;
    GetDiskFreeSpaceExA("C:\\", &free_bytes, &total_bytes, &total_free);

    DiskInfo d;
    d.total_gb = total_bytes.QuadPart / (1024.0 * 1024.0 * 1024.0);
    d.used_gb = (total_bytes.QuadPart - free_bytes.QuadPart) / (1024.0 * 1024.0 * 1024.0);
    d.percent = (d.used_gb / d.total_gb) * 100.0;
    return d;
}

// ── Processes ────────────────────────────────────────────────────────────────

struct ProcessInfo
{
    std::string name;
    DWORD pid;
    double ram_mb;
};

std::vector<ProcessInfo> get_processes(int top_n = 10)
{
    std::vector<ProcessInfo> procs;

    HANDLE snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snap == INVALID_HANDLE_VALUE)
        return procs;

    PROCESSENTRY32 pe;
    pe.dwSize = sizeof(pe);

    if (Process32First(snap, &pe))
    {
        do
        {
            ProcessInfo p;
            p.name = pe.szExeFile;
            p.pid = pe.th32ProcessID;
            p.ram_mb = 0.0;

            HANDLE h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, FALSE, pe.th32ProcessID);
            if (h)
            {
                PROCESS_MEMORY_COUNTERS pmc;
                if (GetProcessMemoryInfo(h, &pmc, sizeof(pmc)))
                {
                    p.ram_mb = pmc.WorkingSetSize / (1024.0 * 1024.0);
                }
                CloseHandle(h);
            }
            procs.push_back(p);
        } while (Process32Next(snap, &pe));
    }

    CloseHandle(snap);

    // sort by RAM descending
    std::sort(procs.begin(), procs.end(), [](const ProcessInfo &a, const ProcessInfo &b)
              { return a.ram_mb > b.ram_mb; });

    if ((int)procs.size() > top_n)
        procs.resize(top_n);
    return procs;
}

// ── JSON output ───────────────────────────────────────────────────────────────

std::string escape_json(const std::string &s)
{
    std::string out;
    for (char c : s)
    {
        if (c == '"')
            out += "\\\"";
        else if (c == '\\')
            out += "\\\\";
        else
            out += c;
    }
    return out;
}

int main()
{
    auto cpu = get_cpu_usage();
    auto ram = get_ram();
    auto disk = get_disk();
    auto procs = get_processes(10);

    std::ostringstream json;
    json << std::fixed << std::setprecision(1);
    json << "{\n";
    json << "  \"cpu_usage\": " << cpu << ",\n";
    json << "  \"ram_used_mb\": " << ram.used_mb << ",\n";
    json << "  \"ram_total_mb\": " << ram.total_mb << ",\n";
    json << "  \"ram_percent\": " << ram.percent << ",\n";
    json << "  \"disk_used_gb\": " << disk.used_gb << ",\n";
    json << "  \"disk_total_gb\": " << disk.total_gb << ",\n";
    json << "  \"disk_percent\": " << disk.percent << ",\n";
    json << "  \"processes\": [\n";

    for (size_t i = 0; i < procs.size(); i++)
    {
        json << "    {\"name\": \"" << escape_json(procs[i].name)
             << "\", \"pid\": " << procs[i].pid
             << ", \"ram_mb\": " << procs[i].ram_mb << "}";
        if (i + 1 < procs.size())
            json << ",";
        json << "\n";
    }

    json << "  ]\n}";

    std::cout << json.str() << std::endl;
    return 0;
}
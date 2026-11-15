// Fleet bridge: three AgOpenWeb virtual tractors in one process.
//
//   coordinator (Python) --commands, UDP text--> 127.0.0.1:10100
//   tractor N            --$PANDA GPS, 10 Hz---> 127.0.0.1:1000N   (10001 / 10002 / 10003)
//
// Commands (one per datagram, space separated, invariant culture):
//   place <id> <lat> <lon> [headingDeg]     put a tractor somewhere, stopped
//   goto  <id> <lat> <lon> <speedKmh>       drive straight to a point, then stop there
//   path  <id> <speedKmh> <lat> <lon> ...   drive through the points in order (a curved turn)
//   stop  <id>                              stop where it is
//   rate  <x>                               simulation speed multiplier (1 = real time)

using System.Diagnostics;
using System.Globalization;
using System.Net;
using System.Net.Sockets;
using System.Text;
using AgOpenWeb.VirtualModules;

const int CommandPort = 10100;
const int FirstGpsPort = 10001;      // tractor N sends to FirstGpsPort + N - 1
const int FirstModulePort = 11001;   // each hub takes 3 consecutive module ports
const int TickMs = 100;              // 10 Hz, the GPS receiver's update rate

var inv = CultureInfo.InvariantCulture;
var tractors = new Tractor[3];
for (int i = 0; i < tractors.Length; i++)
{
    var hub = new VirtualModuleHub(FirstGpsPort + i, FirstModulePort + i * 10);
    hub.Gps.Latitude = 32.6927; hub.Gps.Longitude = -114.6277 + i * 0.0002;   // until the coordinator places it
    tractors[i] = new Tractor(i + 1, hub);
    Console.WriteLine($"Tractor {i + 1}: $PANDA -> 127.0.0.1:{hub.HostReceivePort}");
}

double rate = 1.0;
var gate = new object();

// Command listener.
using var cmdSocket = new UdpClient(new IPEndPoint(IPAddress.Loopback, CommandPort));
Console.WriteLine($"Commands   <- 127.0.0.1:{CommandPort}");
_ = Task.Run(async () =>
{
    while (true)
    {
        try
        {
            var packet = await cmdSocket.ReceiveAsync();
            var text = Encoding.ASCII.GetString(packet.Buffer).Trim();
            lock (gate) { if (!Handle(text)) Console.WriteLine($"? {text}"); }
        }
        catch (SocketException) { /* a closed peer on Windows; keep listening */ }
        catch (ObjectDisposedException) { return; }
    }
});

// Physics + GPS output: every tractor steps and sends one sentence per tick, moving or not.
var clock = Stopwatch.StartNew();
double last = 0;
while (true)
{
    double now = clock.Elapsed.TotalSeconds, dt = Math.Min(0.5, now - last);
    last = now;
    lock (gate) { foreach (var t in tractors) t.Tick(dt * rate); }
    await Task.Delay(TickMs);
}

bool Handle(string text)
{
    var a = text.Split(' ', StringSplitOptions.RemoveEmptyEntries);
    if (a.Length < 2) return false;
    if (a[0] == "rate")
    {
        if (!double.TryParse(a[1], NumberStyles.Float, inv, out var r) || r <= 0) return false;
        rate = Math.Min(r, 200);
        Console.WriteLine($"rate {rate.ToString(inv)}x");
        return true;
    }
    if (!int.TryParse(a[1], out var id) || id < 1 || id > tractors.Length) return false;
    var t = tractors[id - 1];
    double N(int i) => double.Parse(a[i], NumberStyles.Float, inv);
    try
    {
        switch (a[0])
        {
            case "place" when a.Length >= 4:
                t.Place(N(2), N(3), a.Length >= 5 ? N(4) : (double?)null);
                Console.WriteLine($"T{id} placed {a[2]},{a[3]}");
                return true;
            case "goto" when a.Length >= 5:
                t.Follow(N(4), [(N(2), N(3))]);
                return true;
            case "path" when a.Length >= 5 && (a.Length - 3) % 2 == 0:
            {
                var pts = new List<(double, double)>();
                for (int i = 3; i + 1 < a.Length; i += 2) pts.Add((N(i), N(i + 1)));
                t.Follow(N(2), pts);
                return true;
            }
            case "stop":
                t.Stop();
                return true;
        }
    }
    catch (FormatException) { }
    return false;
}

sealed class Tractor(int id, VirtualModuleHub hub)
{
    public int Id { get; } = id;
    private readonly VirtualGpsReceiver _gps = hub.Gps;
    private readonly Queue<(double Lat, double Lon)> _path = new();
    private double _speedKmh;

    public void Place(double lat, double lon, double? headingDeg)
    {
        _path.Clear();
        _gps.Latitude = lat; _gps.Longitude = lon; _gps.SpeedKnots = 0;
        if (headingDeg is { } h) _gps.HeadingDegrees = (h % 360 + 360) % 360;
    }

    /// <summary>Drive through the points in order and stop on the last one. A turn is sent as
    /// closely spaced points along a curve, so the heading swings round smoothly.</summary>
    public void Follow(double speedKmh, IEnumerable<(double, double)> points)
    {
        _path.Clear();
        foreach (var p in points) _path.Enqueue(p);
        _speedKmh = Math.Clamp(speedKmh, 0.5, 60);
    }

    public void Stop() { _path.Clear(); _gps.SpeedKnots = 0; }

    /// <summary>Advance by <paramref name="dt"/> simulated seconds and send one GPS sentence.</summary>
    public void Tick(double dt)
    {
        double speedMs = _speedKmh / 3.6, budget = speedMs * dt;   // metres to cover this tick
        while (_path.Count > 0 && budget > 1e-9)
        {
            var (tLat, tLon) = _path.Peek();
            // Same flat-earth metres as VirtualGpsReceiver.Step.
            double dN = (tLat - _gps.Latitude) * 111320.0;
            double dE = (tLon - _gps.Longitude) * 111320.0 * Math.Cos(_gps.Latitude * Math.PI / 180.0);
            double dist = Math.Sqrt(dN * dN + dE * dE);
            if (dist > 0.01) _gps.HeadingDegrees = (Math.Atan2(dE, dN) * 180.0 / Math.PI + 360) % 360;
            if (budget >= dist)
            {
                _gps.Latitude = tLat; _gps.Longitude = tLon;      // reach the point exactly, carry on to the next
                budget -= dist;
                _path.Dequeue();
            }
            else
            {
                _gps.SpeedKnots = _speedKmh / 1.852;
                _gps.Step(budget / speedMs);
                budget = 0;
            }
        }
        _gps.SpeedKnots = _path.Count > 0 ? _speedKmh / 1.852 : 0;
        _gps.SendOnce();
    }
}

# 4d-plugin-shlwapi-test-path-name

A Windows plugin command that does the same job as 4D's built-in `Test path name`: it tells you whether a path points to a folder, a document, or nothing. The difference is that the check runs on a separate Windows thread, using the Shell Lightweight API (`PathIsDirectory` and `PathFileExists` from `Shlwapi.dll`). While Windows tries to reach a slow or disconnected network share, the calling 4D process keeps yielding, so the rest of the application doesn't freeze. The result is a `Longint`.

| Command | Returns | Purpose |
|:--|:--|:--|
| [shlwapi Test path name](#shlwapi-test-path-name-1) | `Longint` | Test whether a Windows path is a folder, a document, or doesn't exist, without blocking 4D |

**Platforms:** Windows (64-bit). On macOS the command loads but always returns `-43`.

---

## Requirements & platform notes

- **Windows only.** All the work is inside the plugin's Windows branch. On macOS the command never inspects the path and always returns `-43`. Use 4D's own `Test path name` there.
- **Thread-safe.** The manifest declares the command `threadSafe`, so you can call it from preemptive processes and workers.
- **Pass a Windows system path**, not a POSIX path: drive letters, backslashes, UNC paths. If you have a 4D `File` or `Folder` object, pass its `.platformPath`.
- **Failure is silent.** The command never raises a 4D error. Every failure, including an empty path, a path that is too long, and an internal error, returns `-43`, the same value as "doesn't exist". See [Error handling & troubleshooting](#error-handling--troubleshooting).
- **Don't run simultaneous calls.** Every call shares the same named Windows memory mappings, so two calls running at the same time can get each other's results. If several processes may call it at once, serialize them (see the example below).

---

## shlwapi Test path name

### Syntax

```4d
result:=shlwapi Test path name ( path )
```

| Parameter | Type | Description |
|:--|:--|:--|
| `path` | Text | Windows system path of the item to test. Drive paths, UNC paths and the `\\?\` prefix are accepted. |
| Result | Longint | `0` (`Is a folder`), `1` (`Is a document`), or `-43` (not found, or the test could not be done) |

### Description

`shlwapi Test path name` checks `path` on a separate Windows thread and returns what it found:

| Value | Constant | Meaning |
|:--|:--|:--|
| `0` | `Is a folder` | `PathIsDirectory` succeeded: `path` is a folder. |
| `1` | `Is a document` | `PathFileExists` succeeded and `path` is not a folder. |
| `-43` | — | Neither function found `path`, or the command could not run the test. |

The folder test runs first because `PathFileExists` can return false for a shared folder. If `PathIsDirectory` fails, `PathFileExists` decides between `1` and `-43`.

While the thread works, the calling process waits in 100 ms steps and calls `IDLE` between them. Other 4D processes keep running even if Windows takes tens of seconds to time out on an unreachable server. The **calling** process still waits, so `shlwapi Test path name` returns only once Windows has answered.

These path forms are accepted:

- A drive path: `"Z:\\Shared folder"`
- A long-path drive path: `"\\\\?\\Z:\\Shared folder"`
- A UNC path: `"\\\\server\\share\\folder"`
- A long-path UNC path: `"\\\\?\\UNC\\server\\share\\folder"`

**Paths of 32,767 characters or more** return `-43` without being tested. Microsoft documents a `MAX_PATH` (260-character) limit for the Shlwapi path functions themselves. A longer path, even with the `\\?\` prefix, may therefore return `-43` although it exists.

**Relative paths** are resolved against the process's current Windows directory, which you don't control from 4D. Always pass a full path.

### Example

From the plugin's own test method (`Method1.4dm`). The folder name was saved with the wrong text encoding, so it appears garbled. Each assignment to `$path` replaces the previous one, so only the last form is tested:

```4d
$path:="\\\\?\\Z:\\É_ÉEÉìÉçÅ[Éh"
$path:="Z:\\É_ÉEÉìÉçÅ[Éh"
//$path:="\\\\vmware-host\\Shared Folders\\É_ÉEÉìÉçÅ[Éh"
$path:="\\\\?\\UNC\\vmware-host\\Shared Folders\\É_ÉEÉìÉçÅ[Éh"
$test:=shlwapi Test path name($path)
```

Handle each result:

```4d
var $path : Text
var $kind : Integer

$path:="\\\\fileserver\\share\\reports"
$kind:=shlwapi Test path name($path)

Case of
	: ($kind=Is a folder)
		// the share is reachable and reports is a folder
	: ($kind=Is a document)
		// reports is a file
	Else
		// -43: missing, unreachable, or the test could not run
End case
```

Test a 4D `Folder` object by passing its platform path:

```4d
var $folder : 4D.Folder
$folder:=Folder(fk desktop folder)

If (shlwapi Test path name($folder.platformPath)=Is a folder)
	// ...
End if
```

Serialize calls when several processes may test paths at the same time:

```4d
var $kind : Integer
$kind:=-43

If (Not(Semaphore("shlwapi_test_path_name"; 600)))
	$kind:=shlwapi Test path name($path)
	CLEAR SEMAPHORE("shlwapi_test_path_name")
End if
```

---

## Error handling & troubleshooting

- **`-43` doesn't prove the item is missing.** An empty path, a path of 32,767 characters or more, an internal failure, and running on macOS all return `-43` too. If the difference matters, check the path's form before calling.
- **Simultaneous calls can mix up results.** All calls share the same named Windows memory mappings. Calls from several processes at once, or from two 4D applications in the same Windows session, can read each other's input or output. Serialize calls with a semaphore.
- **The calling process still waits.** Other processes keep running, but the process that called the command waits until Windows answers. For an unreachable server, that can take as long as the Windows network timeout. Call it from a separate process or worker if that process must stay responsive.
- **Long paths may fail.** The Shlwapi functions are documented for paths up to 260 characters. A longer path can return `-43` even with the `\\?\` prefix.
- **macOS always returns `-43`.** Use the built-in `Test path name` on macOS.
- **Use backslashes.** Pass a Windows system path. A POSIX path (`/Users/...`) or a 4D filesystem path (`/RESOURCES/...`) returns `-43`. For `File`/`Folder` objects, pass `.platformPath`.

---

## Quick reference

```4d
$kind:=shlwapi Test path name("Z:\\Shared folder")  // 0 folder, 1 document, -43 not found
$kind:=shlwapi Test path name("\\\\?\\UNC\\server\\share\\folder")
$kind:=shlwapi Test path name(Folder(fk desktop folder).platformPath)

If (Not(Semaphore("shlwapi_test_path_name"; 600)))
	$kind:=shlwapi Test path name($path)
	CLEAR SEMAPHORE("shlwapi_test_path_name")
End if
```

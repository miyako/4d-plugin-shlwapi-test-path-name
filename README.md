# 4d-plugin-shlwapi-test-path-name
[Test path name](http://doc.4d.com/4Dv17/4D/17/Test-path-name.301-3729540.en.html) in a new thread (avoid blocking 4D while the system attempts to mount shared volume)

### Platform

| carbon | cocoa | win32 | win64 |
|:------:|:-----:|:---------:|:---------:|
|||<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|<img src="https://cloud.githubusercontent.com/assets/1725068/22371562/1b091f0a-e4db-11e6-8458-8653954a7cce.png" width="24" height="24" />|

### Version

<img src="https://cloud.githubusercontent.com/assets/1725068/18940649/21945000-8645-11e6-86ed-4a0f800e5a73.png" width="32" height="32" /> <img src="https://cloud.githubusercontent.com/assets/1725068/18940648/2192ddba-8645-11e6-864d-6d5692d55717.png" width="32" height="32" /> <img src="https://user-images.githubusercontent.com/1725068/41266195-ddf767b2-6e30-11e8-9d6b-2adf6a9f57a5.png" width="32" height="32" /> 

![preemption xx](https://user-images.githubusercontent.com/1725068/41327179-4e839948-6efd-11e8-982b-a670d511e04f.png)

### Releases

[1.0](https://github.com/miyako/4d-plugin-shlwapi-test-path-name/releases/tag/1.0)

## Syntax

```
result:=shlwapi Test path name (path)
```

Parameter|Type|Description
------------|------------|----
result|LONGINT|``Is a document (1)``, ``Is a folder (0)`` or ``-43``
path|TEXT|

### About

``Test path name:C476``と同等のシステム関数を別スレッドでコールします。

パスには[UNC](https://msdn.microsoft.com/ja-jp/library/gg465305.aspx)を含むシステムパス名を指定します。

例：

* "Z:\\共有フォルダー"
* "\\\\?\\Z:\\共有フォルダー"
* "\\\\共有サーバー\\共有フォルダー"
* "\\\\?\\UNC\\共有サーバー\\共有フォルダー"

（リテラル文字列にエスケープシーケンスを含めた場合）

https://docs.microsoft.com/ja-jp/windows/desktop/FileIO/naming-a-file

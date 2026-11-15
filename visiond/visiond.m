// One-shot vision helper. It runs, does one thing and exits, so the camera is open only while a picture is being taken.
//   julius-visiond camera OUT.jpg     one camera frame -> OUT.jpg. Prints "ok WxH" or "error <why>".
//   julius-visiond faces              reads one image (JPEG or PNG bytes) on stdin, prints {"w":N,"h":N,"faces":[[x,y,w,h],...]} with each box
//        in 0..1 of the image, origin top-left, largest first. Apple Vision, on this Mac only; nothing is written anywhere.
//   julius-visiond landmarks MASK.png  reads one image on stdin; prints {"w","h","faces":[{"box":[x,y,w,h],"roll":r,"points":{"contour":[[x,y],..],"left_eye":..,"right_eye":..,"left_brow":..,"right_brow":..,"nose":..,"mouth":..,"pupils":..}}]}
//        (all fractions of the image, origin top-left, largest face first) and writes the person-segmentation mask (white = person) to MASK.png. Local only.
//   julius-visiond status [owner,owner,...]
//        prints one JSON line: {"locked":bool,"secure_input":bool,"window":{"id":N,"owner":"..","title":"..","w":N,"h":N}|null}
//        window = the frontmost normal on-screen window whose owner is not in the exclude list (Julius's own orb and panel).
//        secure_input is true while any password field has keyboard focus (macOS secure event input).
#import <AVFoundation/AVFoundation.h>
#import <CoreImage/CoreImage.h>
#import <Carbon/Carbon.h>
#import <Vision/Vision.h>
#import <Foundation/Foundation.h>

@interface Grab : NSObject <AVCaptureVideoDataOutputSampleBufferDelegate>
@property int frames;
@property (strong) CIImage *image;
@property (strong) dispatch_semaphore_t done;
@end
@implementation Grab
- (void)captureOutput:(AVCaptureOutput *)o didOutputSampleBuffer:(CMSampleBufferRef)sb fromConnection:(AVCaptureConnection *)c {
    self.frames++;
    if (self.frames < 12) return;  // let auto-exposure and white balance settle (~0.4 s)
    if (self.image) return;
    CVImageBufferRef buf = CMSampleBufferGetImageBuffer(sb);
    if (buf) { self.image = [CIImage imageWithCVImageBuffer:buf]; dispatch_semaphore_signal(self.done); }
}
@end

static int camera(const char *out) {
    AVAuthorizationStatus st = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
    if (st == AVAuthorizationStatusNotDetermined) {
        dispatch_semaphore_t s = dispatch_semaphore_create(0);
        [AVCaptureDevice requestAccessForMediaType:AVMediaTypeVideo completionHandler:^(BOOL ok) { dispatch_semaphore_signal(s); }];
        dispatch_semaphore_wait(s, dispatch_time(DISPATCH_TIME_NOW, 60 * NSEC_PER_SEC));
        st = [AVCaptureDevice authorizationStatusForMediaType:AVMediaTypeVideo];
    }
    if (st != AVAuthorizationStatusAuthorized) { puts("error camera-permission"); return 3; }
    AVCaptureDevice *dev = [AVCaptureDevice defaultDeviceWithMediaType:AVMediaTypeVideo];
    if (!dev) { puts("error no-camera"); return 4; }
    NSError *err = nil;
    AVCaptureDeviceInput *in = [AVCaptureDeviceInput deviceInputWithDevice:dev error:&err];
    if (!in) { puts("error camera-busy"); return 5; }
    AVCaptureSession *session = [[AVCaptureSession alloc] init];
    session.sessionPreset = AVCaptureSessionPreset1280x720;
    AVCaptureVideoDataOutput *vo = [[AVCaptureVideoDataOutput alloc] init];
    Grab *g = [[Grab alloc] init]; g.done = dispatch_semaphore_create(0);
    dispatch_queue_t q = dispatch_queue_create("julius.visiond", DISPATCH_QUEUE_SERIAL);
    [vo setSampleBufferDelegate:g queue:q];
    if (![session canAddInput:in] || ![session canAddOutput:vo]) { puts("error camera-config"); return 6; }
    [session addInput:in]; [session addOutput:vo];
    [session startRunning];
    long timedOut = dispatch_semaphore_wait(g.done, dispatch_time(DISPATCH_TIME_NOW, 10 * NSEC_PER_SEC));
    [session stopRunning];  // the indicator goes off here
    if (timedOut || !g.image) { puts("error no-frame"); return 7; }
    CIContext *ctx = [CIContext contextWithOptions:nil];
    CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
    NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:out]];
    BOOL ok = [ctx writeJPEGRepresentationOfImage:g.image toURL:url colorSpace:cs options:@{(id)kCGImageDestinationLossyCompressionQuality: @0.9} error:&err];
    CGColorSpaceRelease(cs);
    if (!ok) { puts("error write"); return 8; }
    printf("ok %dx%d\n", (int)g.image.extent.size.width, (int)g.image.extent.size.height);
    return 0;
}

static int faces(void) {
    NSData *data = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
    if (!data.length) { puts("error no-image"); return 3; }
    CIImage *ci = [CIImage imageWithData:data];
    if (!ci) { puts("error bad-image"); return 4; }
    VNDetectFaceRectanglesRequest *req = [[VNDetectFaceRectanglesRequest alloc] init];
    VNImageRequestHandler *h = [[VNImageRequestHandler alloc] initWithData:data options:@{}];
    NSError *err = nil;
    if (![h performRequests:@[req] error:&err]) { puts("error detect"); return 5; }
    NSMutableArray *boxes = [NSMutableArray array];
    for (VNFaceObservation *f in [req.results sortedArrayUsingComparator:^NSComparisonResult(VNFaceObservation *a, VNFaceObservation *b) {
        double A = a.boundingBox.size.width * a.boundingBox.size.height, B = b.boundingBox.size.width * b.boundingBox.size.height;
        return A < B ? NSOrderedDescending : A > B ? NSOrderedAscending : NSOrderedSame; }]) {
        CGRect r = f.boundingBox;  // normalised, origin bottom-left
        [boxes addObject:@[@(r.origin.x), @(1 - r.origin.y - r.size.height), @(r.size.width), @(r.size.height)]];
    }
    NSDictionary *out = @{@"w": @((int)ci.extent.size.width), @"h": @((int)ci.extent.size.height), @"faces": boxes};
    NSData *d = [NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
    printf("%s\n", [[[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] UTF8String]);
    return 0;
}

static NSArray *region(VNFaceLandmarkRegion2D *r, CGRect b) {
    NSMutableArray *a = [NSMutableArray array];
    if (!r) return a;
    const CGPoint *pts = r.normalizedPoints;
    for (NSUInteger i = 0; i < r.pointCount; i++) {  // normalised to the face box, origin bottom-left
        double x = b.origin.x + pts[i].x * b.size.width, y = b.origin.y + pts[i].y * b.size.height;
        [a addObject:@[@(x), @(1 - y)]];
    }
    return a;
}

static int landmarks(const char *maskOut) {
    NSData *data = [[NSFileHandle fileHandleWithStandardInput] readDataToEndOfFile];
    if (!data.length) { puts("error no-image"); return 3; }
    CIImage *ci = [CIImage imageWithData:data];
    if (!ci) { puts("error bad-image"); return 4; }
    VNDetectFaceLandmarksRequest *req = [[VNDetectFaceLandmarksRequest alloc] init];
    VNGeneratePersonSegmentationRequest *seg = [[VNGeneratePersonSegmentationRequest alloc] init];
    seg.qualityLevel = VNGeneratePersonSegmentationRequestQualityLevelAccurate;
    seg.outputPixelFormat = kCVPixelFormatType_OneComponent8;
    VNImageRequestHandler *h = [[VNImageRequestHandler alloc] initWithData:data options:@{}];
    NSError *err = nil;
    if (![h performRequests:@[req, seg] error:&err]) { puts("error detect"); return 5; }
    NSMutableArray *faces = [NSMutableArray array];
    for (VNFaceObservation *f in [req.results sortedArrayUsingComparator:^NSComparisonResult(VNFaceObservation *a, VNFaceObservation *b) {
        double A = a.boundingBox.size.width * a.boundingBox.size.height, B = b.boundingBox.size.width * b.boundingBox.size.height;
        return A < B ? NSOrderedDescending : A > B ? NSOrderedAscending : NSOrderedSame; }]) {
        CGRect r = f.boundingBox;
        VNFaceLandmarks2D *L = f.landmarks;
        if (!L) continue;
        NSDictionary *pts = @{
            @"contour": region(L.faceContour, r), @"left_eye": region(L.leftEye, r), @"right_eye": region(L.rightEye, r),
            @"left_brow": region(L.leftEyebrow, r), @"right_brow": region(L.rightEyebrow, r), @"nose": region(L.nose, r),
            @"mouth": region(L.outerLips, r), @"inner_mouth": region(L.innerLips, r),
            @"left_pupil": region(L.leftPupil, r), @"right_pupil": region(L.rightPupil, r)};
        [faces addObject:@{@"box": @[@(r.origin.x), @(1 - r.origin.y - r.size.height), @(r.size.width), @(r.size.height)],
                           @"roll": f.roll ?: @0, @"points": pts}];
    }
    BOOL mask = NO;
    VNPixelBufferObservation *m = (VNPixelBufferObservation *)seg.results.firstObject;
    if (m && maskOut && *maskOut) {
        CIImage *mi = [CIImage imageWithCVPixelBuffer:m.pixelBuffer];
        CIContext *ctx = [CIContext contextWithOptions:nil];
        CGColorSpaceRef cs = CGColorSpaceCreateWithName(kCGColorSpaceSRGB);
        NSURL *url = [NSURL fileURLWithPath:[NSString stringWithUTF8String:maskOut]];
        mask = [ctx writePNGRepresentationOfImage:mi toURL:url format:kCIFormatRGBA8 colorSpace:cs options:@{} error:&err];
        CGColorSpaceRelease(cs);
    }
    NSDictionary *out = @{@"w": @((int)ci.extent.size.width), @"h": @((int)ci.extent.size.height), @"faces": faces, @"mask": @(mask)};
    NSData *d = [NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
    printf("%s\n", [[[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] UTF8String]);
    return 0;
}

static int status(const char *exclude) {
    NSMutableSet *skip = [NSMutableSet set];
    if (exclude && *exclude) for (NSString *n in [[NSString stringWithUTF8String:exclude] componentsSeparatedByString:@","]) [skip addObject:n];
    CFDictionaryRef sess = CGSessionCopyCurrentDictionary();
    BOOL locked = NO;
    if (sess) { locked = [[(__bridge NSDictionary *)sess objectForKey:@"CGSSessionScreenIsLocked"] boolValue]; CFRelease(sess); }
    NSDictionary *win = nil;
    CFArrayRef list = CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements, kCGNullWindowID);
    for (NSDictionary *w in (__bridge NSArray *)list) {  // front to back
        if ([w[(id)kCGWindowLayer] intValue] != 0) continue;
        if ([w[(id)kCGWindowAlpha] doubleValue] <= 0.05) continue;
        NSString *owner = w[(id)kCGWindowOwnerName] ?: @"";
        if ([skip containsObject:owner]) continue;
        CGRect r; CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)w[(id)kCGWindowBounds], &r);
        if (r.size.width < 100 || r.size.height < 100) continue;
        win = @{@"id": w[(id)kCGWindowNumber], @"owner": owner, @"title": w[(id)kCGWindowName] ?: @"", @"w": @((int)r.size.width), @"h": @((int)r.size.height)};
        break;
    }
    if (list) CFRelease(list);
    NSDictionary *out = @{@"locked": @(locked), @"secure_input": @((BOOL)IsSecureEventInputEnabled()), @"window": win ?: [NSNull null]};
    NSData *d = [NSJSONSerialization dataWithJSONObject:out options:0 error:nil];
    printf("%s\n", [[[NSString alloc] initWithData:d encoding:NSUTF8StringEncoding] UTF8String]);
    return 0;
}

int main(int argc, char **argv) {
    @autoreleasepool {
        if (argc >= 3 && !strcmp(argv[1], "camera")) return camera(argv[2]);
        if (argc >= 2 && !strcmp(argv[1], "faces")) return faces();
        if (argc >= 2 && !strcmp(argv[1], "landmarks")) return landmarks(argc >= 3 ? argv[2] : "");
        if (argc >= 2 && !strcmp(argv[1], "status")) return status(argc >= 3 ? argv[2] : "");
        puts("error usage"); return 2;
    }
}

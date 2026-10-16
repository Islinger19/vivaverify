# Smart Attendance System using Face Recognition (sample mini-project report)

This is a fictional sample report used to demonstrate and test VivaVerify.

## 1. Problem

In our department a 60-minute lecture loses about 8 to 10 minutes to manual roll call, and proxy attendance (a friend answering for an absent student) is common in large classes of 60 or more students. Faculty also have to type the paper register into the ERP every week, which causes data-entry mistakes. We wanted a system that marks attendance automatically when students enter the class, without extra hardware for every student such as RFID cards.

## 2. Design choices

We used the open-source face_recognition library (built on dlib) instead of training our own CNN. Training a CNN needs hundreds of images per student, but we could only collect 5 photos per student, and face_recognition already gives a 128-dimensional face embedding from a pre-trained ResNet model. Recognition is then a nearest-neighbour comparison between embeddings.

For face detection we chose the HOG detector instead of the CNN detector, because HOG runs in real time on a Raspberry Pi 4 CPU while the CNN detector took more than 3 seconds per frame. We set the matching tolerance to 0.5 instead of the default 0.6, because 0.6 caused two pairs of similar-looking students to be confused during our trial.

To stop proxy attendance with a printed photo or a phone screen, we added a liveness check based on blink detection. We compute the eye aspect ratio (EAR) from facial landmarks and only mark a student present if at least one blink is seen within 3 seconds (EAR drops below 0.21).

## 3. Implementation

The hardware is a Raspberry Pi 4 (4 GB) with a Pi Camera Module v2 fixed above the classroom door. A Python script captures frames at 640x480, detects faces with HOG, checks liveness, computes embeddings and compares them with the stored embeddings of the enrolled class.

Enrollment is done once: each student gives 5 photos, and their embeddings are averaged and saved as a NumPy .npy file, so we never store the raw photos on the device. Attendance records (roll number, timestamp, lecture id) are written to a local SQLite database. A small Flask web dashboard lets the faculty member see the live attendance list, correct mistakes manually, and export a CSV file that matches the ERP upload format.

To avoid duplicate entries, a student is marked only once per lecture: the script keeps a set of roll numbers already marked for the current lecture id.

## 4. Testing and results

We tested the system with 42 students over 6 lectures in three lighting conditions. Recognition accuracy was 94.6 percent in normal classroom light, 89.3 percent near the window with backlight, and 81.0 percent in low light. The false-accept rate (marking the wrong student) was 1.2 percent. Average processing time was 0.8 seconds per face on the Raspberry Pi 4. The liveness check rejected 19 out of 20 printed-photo attempts and all 10 phone-screen attempts. Roll call time dropped from about 9 minutes to under 1 minute per lecture.

## 5. Limitations and future work

Accuracy drops in low light and when students wear masks, and the system cannot reliably separate identical twins. Students who enter in a crowd may be missed if their face is hidden. In future we plan to add an infrared camera for low light, use a second camera angle, and send a notification to students who were not detected so they can raise a correction request instead of being marked absent.
